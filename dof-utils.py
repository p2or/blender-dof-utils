# ##### BEGIN GPL LICENSE BLOCK #####
#
#  This program is free software; you can redistribute it and/or
#  modify it under the terms of the GNU General Public License
#  as published by the Free Software Foundation; either version 2
#  of the License, or (at your option) any later version.
#
#  This program is distributed in the hope that it will be useful,
#  but WITHOUT ANY WARRANTY; without even the implied warranty of
#  MERCHANTABILITY or FITNESS FOR A PARTICULAR PURPOSE.  See the
#  GNU General Public License for more details.
#
#  You should have received a copy of the GNU General Public License
#  along with this program; if not, write to the Free Software Foundation,
#  Inc., 51 Franklin Street, Fifth Floor, Boston, MA 02110-1301, USA.
#
# ##### END GPL LICENSE BLOCK #####

bl_info = {
    "name": "Depth of Field Utilities",
    "author": "Christian Brinkmann (p2or)",
    "description": "Displays depth of field in 3D viewport.",
    "version": (0, 1, 3),
    "blender": (4, 0, 0),
    "location": "3d View > Properties Panel (N) > Depth of Field Utilities",
    "doc_url": "https://github.com/p2or/blender-dof-utils",
    "tracker_url": "https://github.com/p2or/blender-dof-utils/issues",
    "category": "Render"
}

import bpy
import blf
import gpu
from gpu_extras.batch import batch_for_shader
import math
from mathutils import Matrix, Vector
from mathutils.geometry import intersect_point_line


# ------------------------------------------------------------------------
#   Preferences & Scene Properties
# ------------------------------------------------------------------------

class DOFU_AP_preferences(bpy.types.AddonPreferences):

    bl_idname = __name__
    
    display_info: bpy.props.BoolProperty(
            name="Display Infos in Viewport",
            default = True)
    
    display_limits: bpy.props.BoolProperty(
        name="Display Limits in Viewport Header",
        description="Displays distance, near & far limits in viewport header",
        default = True)
    
    def draw(self, context):
        layout = self.layout
        row = layout.row(align=True)
        row.prop(self, "display_info")
        row.prop(self, "display_limits")
        layout.row().operator("dof_utils.reset_preferences", icon='FILE_REFRESH')


class DOFU_PG_settings(bpy.types.PropertyGroup):

    _visualize_handle = None 
    _instructions_handle = None

    use_cursor: bpy.props.BoolProperty(
        name="Use 3d Cursor Flag",
        description="",
        default=False,
        options={'SKIP_SAVE'})

    draw_dof: bpy.props.BoolProperty(
        name="Draw DoF Flag",
        description="",
        default=False,
        options={'SKIP_SAVE'})
    
    overlay: bpy.props.BoolProperty(
        name="Line overlay",
        description="Display DoF above all other Elements",
        default=True)

    size_limits: bpy.props.FloatProperty(
        name="Size",
        description="Limit Radius",
        min=0.0,
        step=1,
        default=0.1)

    fill_limits: bpy.props.BoolProperty(
        name="Fill limits",
        description="Fill Limits",
        default=False)
    
    draw_focus: bpy.props.BoolProperty(
        name="Display Focus",
        description="Draw Focus",
        default=False)

    color_limits: bpy.props.FloatVectorProperty(  
       name="Color Limits",
       subtype='COLOR',
       default=(0.0, 1.0, 0.0),
       min=0.0, max=1.0,
       description="color picker")

    segments_limits: bpy.props.IntProperty(  
       name="Segments",
       default=16,
       min=3, max=32)

    opacity_limits: bpy.props.FloatProperty(
        name="Opacity",
        min=0.1, max=1.0,
        step=1,
        default=0.9)

    limits: bpy.props.FloatVectorProperty(
            name="Limits",
            size=3)
    
# ------------------------------------------------------------------------
#   Helper
# ------------------------------------------------------------------------

def is_camera(obj):
    return obj is not None and obj.type == 'CAMERA'

def distance(pt1, pt2):
    pt_apex, pt_base = sorted((pt1, pt2), key=lambda v: v.z, reverse=True)
    return (Vector((pt_apex.x, pt_apex.y, pt_base.z)) - pt_base).length

def fstops(camera_data, magnification):
    """ Return or calculate f-stops (N) """
    # Following lines rem as radius option not exist
    #if camera_data.cycles.aperture_type == 'RADIUS':
    #    #print("Radius:", ((cam.lens / cam.cycles.aperture_fstop) / 2000))
    #    if camera_data.cycles.aperture_size > 0.00001: # division by zero fix
    #        return ((camera_data.lens /1000 * magnification) / (camera_data.cycles.aperture_size *2))
    #    else:
    #        return camera_data.clip_end
    #else:
    return camera_data.dof.aperture_fstop #.cycles.aperture_fstop
       
def dof_calculation(camera_data, dof_distance, magnification=1):
    # https://en.wikipedia.org/wiki/Depth_of_focus#Calculation
    m = 1               # Magnification 
    f = 22 / 1000 * m   # Focal length and unit conversation
    N = 1.7817975283    # Lens f number (1.8)
    c = 0.032           # Circle of Confusion (default full frame)
    d = 3               # Subject distance

    N = fstops(camera_data, magnification)
    f = camera_data.lens / 1000 * magnification

    # Calculate Circle of confusion (diameter limit based on d/1500)
    # https://en.wikipedia.org/wiki/Circle_of_confusion#Circle_of_confusion_diameter_limit_based_on_d.2F1500
    c = math.sqrt(camera_data.sensor_width**2 + camera_data.sensor_height**2) / 1500
    
    # Hyperfocal distance (H)
    # https://en.wikipedia.org/wiki/Hyperfocal_distance#Formulae
    a = math.pow(f, 2) / (N * c * magnification / 1000) # respect the units
    H = a + f
    
    Hn = H / 2                                              # Hyperfocal near limit
    nL = (a * dof_distance) / (a + (dof_distance - f))      # DoF near limit
    if (0.01 > (H - dof_distance)):                         # DoF far limit
        fL = camera_data.clip_end # math.inf
    else:
        fL = (a * dof_distance) / (a - (dof_distance - f)) 
    DoF = fL - nL                                           # Depth of field   
    DoFf = dof_distance - nL                                # Depth in Front
    Dofb = fL - dof_distance                                # Depth Behind
    return (nL, fL)


# ------------------------------------------------------------------------
#   OpenGL callbacks
# ------------------------------------------------------------------------

def draw_callback_3d(operator, context):

    scn = context.scene
    dofu = scn.dof_utils

    if is_camera(context.object):
        cam_ob = context.object
    elif is_camera(scn.camera):
        cam_ob = scn.camera
    else:
        return

    mat = cam_ob.matrix_world    
    cam = cam_ob.data
    nmat = mat.normalized()
    target_scale = (1, 1, 1)
    smat = Matrix()
    for i in range(3):
        smat[i][i] = target_scale[i]
    temp_matrix = nmat @ smat # cam_ob.matrix_world = nmat * smat
   
    start = temp_matrix @ Vector((0, 0, -cam.clip_start))
    end = temp_matrix @ Vector((0, 0, -cam.clip_end))
    d = cam.dof.focus_distance

    if cam.dof.focus_object is None:
        near_limit, far_limit = dof_calculation(cam, d)
        dof_loc = temp_matrix @ Vector((0, 0, -(near_limit)))
        dof_loc_end = temp_matrix @ Vector((0, 0, -(far_limit)))
        
    else:
        pt = cam.dof.focus_object.matrix_world.translation
        loc = intersect_point_line(pt, temp_matrix.translation, temp_matrix @ Vector((0, 0, -1)))      
        d = ((loc[0] - start).length) + cam.clip_start # respect the clipping start value
        
        near_limit, far_limit = dof_calculation(cam, d)
        dof_loc = temp_matrix @ Vector((0, 0, -(near_limit)))
        dof_loc_end = temp_matrix @ Vector((0, 0, -(far_limit)))
    
    dofu.limits = (d, near_limit, far_limit)

    # 80% alpha, 2 pixel width line
    gpu.state.blend_set('ALPHA') # bgl.glEnable(bgl.GL_BLEND)
    #bgl.glEnable(bgl.GL_LINE_SMOOTH) # -> No replacement gpu.shader.from_builtin('POLYLINE_SMOOTH_COLOR')
    gpu.state.depth_test_set("LESS") # bgl.glEnable(bgl.GL_DEPTH_TEST)

    # check overlay
    if dofu.overlay:
        gpu.state.depth_test_set("NONE") # bgl.glDisable(bgl.GL_DEPTH_TEST)
    else:
        gpu.state.depth_test_set("LESS") # bgl.glEnable(bgl.GL_DEPTH_TEST)
    
    # set line width
    gpu.state.line_width_set(2) # bgl.glLineWidth(2)

    def line(color, start, end):
        vertices = [start,end]
        shader = gpu.shader.from_builtin('UNIFORM_COLOR')
        batch = batch_for_shader(shader,'LINE_STRIP', {"pos": vertices})
        shader.bind()
        shader.uniform_float("color", color)
        batch.draw(shader)
        #bgl.glColor4f(*color)
        #bgl.glBegin(bgl.GL_LINES)
        #bgl.glVertex3f(*start)
        #bgl.glVertex3f(*end)
        #bgl.glEnd()
    
    # define the lines
    line((1.0, 1.0, 1.0, 0.1), dof_loc_end, end)
    line((1.0, 1.0, 1.0, 0.1), dof_loc, start)
    line((dofu.color_limits[0], dofu.color_limits[1], dofu.color_limits[2], dofu.opacity_limits), dof_loc_end, dof_loc)

    if dofu.size_limits > 0.0:
        #draw_empty(matrix=temp_matrix, offset=-near_limit, size=1)
        for i in [near_limit, far_limit]:
            draw_circle(
                matrix=temp_matrix, 
                offset=-i, 
                color=(dofu.color_limits[0], dofu.color_limits[1], dofu.color_limits[2], dofu.opacity_limits), 
                radius=dofu.size_limits, 
                fill=dofu.fill_limits,
                num_segments=dofu.segments_limits)

    if dofu.draw_focus:
        draw_empty_2d(
            matrix=temp_matrix,
            offset=-d, 
            size=dofu.size_limits * 1.7,
            color=(dofu.color_limits[0], dofu.color_limits[1], dofu.color_limits[2], dofu.opacity_limits))

    # restore defaults
    gpu.state.line_width_set(1.0)
    gpu.state.blend_set('NONE')

def draw_string(x, y, packed_strings):
    font_id = 0
    blf.size(font_id, 17*(bpy.context.preferences.system.dpi/72))
    x_offset = 0
    for pstr, pcol in packed_strings:
        text_width, text_height = blf.dimensions(font_id, pstr)
        blf.position(font_id, (x + x_offset), y, 0)
        blf.color(font_id, *pcol)
        blf.draw(font_id, pstr)
        x_offset += text_width


def draw_callback_2d(operator, context):
    x, y = (70, 30)
    WHITE = (1, 1, 1, 1)
    GREEN = (0, 1, 0, 1)
    BLUE = (0, 0, 1, 1)

    ps=[("Hit ", WHITE),
        ("ESC ", GREEN),
        ("or ", WHITE),
        ("RMB ", GREEN),
        ("when done", WHITE)
        ]
    draw_string(x, y, ps)

def draw_poly(coords, color, width):
    # Get shader
    shader = gpu.shader.from_builtin('UNIFORM_COLOR')
    # Create batch process
    batch = batch_for_shader(shader,'LINE_STRIP', {"pos": coords})
    # Set the line width
    gpu.state.line_width_set(width) # bgl.glLineWidth(width)
    shader.bind()
    # Set color
    shader.uniform_float("color",color)
    # Draw line
    batch.draw(shader)

    
# Draws a line on the view port being two points
def draw_line_3d(start, end, color=None, width=1):
    # Use default color or color given if possible
    color = (0.0, 0.0, 0.0, 1.0) if color is None else color
    draw_poly([start,end], color, width)

'''
def draw_empty(matrix, size, offset=0, offset_axis="Z", color=None, width=1):
    vector_list = [
        Vector((size, 0, 0)), Vector((-size, 0, 0)), # x
        Vector((0, size, 0)), Vector((0, -size, 0)), # y
        Vector((0, 0, size)), Vector((0, 0, -size))] # z
    
    translate = {
        'X': Vector((offset, 0, 0)), 
        'Y': Vector((0, offset, 0)), 
        'Z': Vector((0, 0, offset))}
    
    origin = matrix * translate[offset_axis] #origin = matrix * Vector((0, 0, 0))
    for v in vector_list:
        end = matrix * (v + translate[offset_axis])
        draw_line_3d(origin, end)
'''

def draw_empty_2d(matrix, size, offset=0, offset_axis="Z", color=None, width=1):
    vector_list = [
        Vector((size, 0, 0)), Vector((-size, 0, 0)), # x
        Vector((0, size, 0)), Vector((0, -size, 0))] # y
    
    translate = {
        'X': Vector((offset, 0, 0)), 
        'Y': Vector((0, offset, 0)), 
        'Z': Vector((0, 0, offset))}
    
    origin = matrix @ translate[offset_axis] #origin = matrix * Vector((0, 0, 0))
    for v in vector_list:
        end = matrix @ (v + translate[offset_axis])
        draw_line_3d(origin, end)

# based on http://slabode.exofire.net/circle_draw.shtml
def draw_circle(matrix, radius=.1, num_segments=16, offset=0, offset_axis="Z", color=None, width=1, fill=False):
    #precalculate the sine and cosine
    theta = 2 * math.pi / num_segments
    c = math.cos(theta)
    s = math.sin(theta)
    x = radius
    y = 0
    
    vector_list = []
    for i in range (num_segments+1):
        vector_list.append(Vector((x, y, 0))) # output vertex
        t = x
        x = c * x - s * y
        y = s * t + c * y
    
    translate = {
        'X': Vector((offset, 0, 0)), 
        'Y': Vector((0, offset, 0)), 
        'Z': Vector((0, 0, offset))}
        
    #if not fill: # bgl.GL_TRIANGLE_FAN, http://www.glprogramming.com/red/chapter02.html
    #    bgl.glBegin(bgl.GL_LINE_LOOP)
    #else:
    #    bgl.glBegin(bgl.GL_TRIANGLE_FAN)
    draw_points = []
    for v in vector_list:
        coord = matrix @ (v + translate[offset_axis])
        draw_points.append(coord)
    
    draw_poly(draw_points, color, width)


# ------------------------------------------------------------------------
#    Operators
# ------------------------------------------------------------------------

class DOFU_OT_focusPicking(bpy.types.Operator):
    """Sets the focus distance by using the 3d cursor"""
    bl_idname = "dof_utils.focus_picking"
    bl_label = "Set Focus using 3d Cursor"
    bl_description = "Sets the focus distance by using the 3d cursor"
    bl_options = {'REGISTER', 'UNDO'}

    _tool = None

    @classmethod
    def poll(cls, context):
        return is_camera(context.object)

    def redraw_viewports(self, context):
        for area in context.screen.areas:
            if area.type == 'VIEW_3D':
                area.tag_redraw()

    def modal(self, context, event):
        scene = context.scene
        dofu = scene.dof_utils
        prefs = context.preferences.addons[__name__].preferences

        if context.area is not None:
            context.area.tag_redraw()

        try:
            # Set cursor tool
            bpy.ops.wm.tool_set_by_id(name ="builtin.cursor")
        except:
            bpy.types.SpaceView3D.draw_handler_remove(DOFU_PG_settings._instructions_handle, 'WINDOW')
            DOFU_PG_settings._instructions_handle = None
            context.scene.dof_utils.use_cursor = False
            return {'CANCELLED'}

        if event.type == 'LEFTMOUSE': 
            if event.value == 'RELEASE':
                context.object.data.dof.focus_distance = \
                    distance(scene.cursor.location, context.object.matrix_world.to_translation())
            return {'PASS_THROUGH'}
        
        elif event.type in {'RIGHTMOUSE', 'ESC'} or not dofu.use_cursor:
            dofu.use_cursor = False
            try:
                bpy.types.SpaceView3D.draw_handler_remove(DOFU_PG_settings._instructions_handle, 'WINDOW')
                DOFU_PG_settings._instructions_handle = None
            except:
                pass
            # Reset to selected tool before running the operator
            bpy.ops.wm.tool_set_by_id(name=self._tool)
            self.redraw_viewports(context)
            return {'CANCELLED'}

        return {'PASS_THROUGH'}
    
    def invoke(self, context, event):
        dofu = context.scene.dof_utils #context.area.tag_redraw()
        prefs = context.preferences.addons[__name__].preferences

        if not dofu.use_cursor:
            if context.area.type == 'VIEW_3D':
                
                # Get the current active tool
                from bl_ui.space_toolsystem_common import ToolSelectPanelHelper
                self._tool = ToolSelectPanelHelper.tool_active_from_context(context).idname

                if prefs.display_info and not DOFU_PG_settings._instructions_handle:
                    args = (self, context)
                    DOFU_PG_settings._instructions_handle = bpy.types.SpaceView3D.draw_handler_add(draw_callback_2d, args, 'WINDOW', 'POST_PIXEL')
                
                context.window_manager.modal_handler_add(self)
                dofu.use_cursor = True
                return {'RUNNING_MODAL'}
            else:
                self.report({'WARNING'}, "View3D not found, cannot run operator")
                return {'CANCELLED'}
        else:
            self.report({'WARNING'}, "Operator is already running")
            return {'CANCELLED'}


class DOFU_OT_visualizeLimits(bpy.types.Operator):
    """ Draws depth of field in the viewport via OpenGL """
    bl_idname = "dof_utils.visualize_dof"
    bl_label = "Visualize Depth of Field"
    bl_description = "Draws depth of field in the vieport via OpenGL"

    @classmethod
    def poll(cls, context):
        #rd = context.scene.render
        return is_camera(context.object) #and rd.engine == "CYCLES"

    def redraw_viewports(self, context):
        for area in context.screen.areas:
            if area.type == 'VIEW_3D':
                area.tag_redraw()

    def modal(self, context, event):
        dofu = context.scene.dof_utils
        prefs = context.preferences.addons[__name__].preferences

        if context.area is not None:
            context.area.tag_redraw()

        if prefs.display_limits and context.area is not None:
            context.area.header_text_set("Focus Distance: %.3f Near Limit: %.3f Far Limit: %.3f" % tuple(dofu.limits))

        if event.type in {'RIGHTMOUSE', 'ESC'} or not dofu.draw_dof:
            dofu.draw_dof = False
            try: # TODO, viewport class
                bpy.types.SpaceView3D.draw_handler_remove(DOFU_PG_settings._visualize_handle, 'WINDOW')
                bpy.types.SpaceView3D.draw_handler_remove(DOFU_PG_settings._instructions_handle, 'WINDOW')
                DOFU_PG_settings._instructions_handle = None
                DOFU_PG_settings._visualize_handle = None
            except:
                pass
            if context.area is not None:
                context.area.header_text_set(text=None)
            self.redraw_viewports(context)
            return {'CANCELLED'}
        
        return {'PASS_THROUGH'}
               
    def invoke(self, context, event):
        dofu = context.scene.dof_utils
        prefs = context.preferences.addons[__name__].preferences

        if not dofu.draw_dof:
            if context.area.type == 'VIEW_3D':
                args = (self, context)
                # Add the region OpenGL drawing callback, draw in view space with 'POST_VIEW' and 'PRE_VIEW'
                DOFU_PG_settings._visualize_handle = bpy.types.SpaceView3D.draw_handler_add(draw_callback_3d, args, 'WINDOW', 'POST_VIEW')

                if prefs.display_info and not DOFU_PG_settings._instructions_handle:
                    DOFU_PG_settings._instructions_handle = bpy.types.SpaceView3D.draw_handler_add(draw_callback_2d, args, 'WINDOW', 'POST_PIXEL')
                
                context.window_manager.modal_handler_add(self)
                dofu.draw_dof = True
                self.redraw_viewports(context)
                return {'RUNNING_MODAL'}

            else:
                self.report({'WARNING'}, "View3D not found, cannot run operator")
                return {'CANCELLED'}
        else:
            self.report({'WARNING'}, "Operator is already running")
            return {'CANCELLED'}


class DOFU_OT_killVisualization(bpy.types.Operator):
    """ Kill Visualization """
    bl_idname = "dof_utils.kill_visualization"
    bl_label = "Kill Visualization"
    bl_description = "Kills Viewport Visualization"

    def execute(self, context):
        context.scene.dof_utils.draw_dof = False
        return {'FINISHED'}


class DOFU_OT_killFocusPicking(bpy.types.Operator):
    """ Kill Focus Picking """
    bl_idname = "dof_utils.kill_focus_picking"
    bl_label = "Kill Visualization"
    bl_description = "Kills Focus Picking"

    def execute(self, context):
        context.scene.dof_utils.use_cursor = False
        return {'FINISHED'}


class DOFU_OT_viewportReset(bpy.types.Operator):
    """ Reset Viewport """
    bl_idname = "dof_utils.reset_viewport"
    bl_label = "Reset Viewport"
    bl_options = {"INTERNAL"}

    # TODO, viewport class
    def execute(self, context):
        try: 
            DOFU_PG_settings._instructions_handle = None
            DOFU_PG_settings._visualize_handle = None
            bpy.types.SpaceView3D.draw_handler_remove(DOFU_PG_settings._instructions_handle, 'WINDOW')
            bpy.types.SpaceView3D.draw_handler_remove(DOFU_PG_settings._visualize_handle, 'WINDOW')
            
        except:
            pass
        return {'FINISHED'}


class DOFU_OT_preferencesReset(bpy.types.Operator):
    """ Reset Add-on Preferences """
    bl_idname = "dof_utils.reset_preferences"
    bl_label = "Reset Properties and Settings"
    bl_options = {"INTERNAL"}

    def execute(self, context):
        scn = context.scene
        dofu = scn.dof_utils
        prefs = context.preferences.addons[__name__].preferences
        prefs.property_unset("display_info")
        prefs.property_unset("display_limits")
        dofu.property_unset("use_cursor")
        dofu.property_unset("draw_dof")
        dofu.property_unset("overlay")
        dofu.property_unset("limits")
        bpy.ops.wm.save_userpref()
        bpy.ops.dof_utils.reset_viewport()
        return {'FINISHED'}


# ------------------------------------------------------------------------
#    UI
# ------------------------------------------------------------------------

class DOFU_panel:
    bl_space_type = "VIEW_3D"  
    bl_region_type = "UI"
    bl_category = "DoF Utils"
    
    @classmethod
    def poll(cls, context):
        return is_camera(context.object)


class DOFU_PT_main_panel(DOFU_panel, bpy.types.Panel):
    bl_label = "Depth of Field"  
    bl_category = "DoF Utils"
    
    def draw_header(self, context):
        self.layout.prop(context.active_object.data.dof, "use_dof", text="")

    def draw(self, context):
        dofu = context.scene.dof_utils #cam_ob = scene.camera.data
        cam_ob = context.active_object.data
        
        layout = self.layout
        layout.use_property_split = True

        layout.active = cam_ob.dof.use_dof
        active_flag = not dofu.use_cursor and cam_ob.dof.focus_object is None
        
        # Visualize
        row = layout.row(align=True)
        viz = row.column(align=True)
        viz.enabled = not dofu.draw_dof # enabled
        viz.operator("dof_utils.visualize_dof", icon="SNAP_NORMAL" if not dofu.draw_dof else "REC")
        row = row.column(align=True)
        row.operator("dof_utils.kill_visualization", icon="X", text="")

        # Focus Picking
        row = layout.row(align=True)
        pic = row.column(align=True)
        pic.enabled = active_flag # enabled
        pic.operator("dof_utils.focus_picking", icon="RESTRICT_SELECT_OFF" if active_flag or cam_ob.dof.focus_object else "REC")
        row = row.column(align=True) #layout.prop_search(dofu, "camera", bpy.data, "cameras")
        row.enabled = cam_ob.dof.focus_object is None
        row.operator("dof_utils.kill_focus_picking", icon="X", text="")


class DOFU_PT_camera(DOFU_panel, bpy.types.Panel):  
    bl_label = "Camera Settings"
    bl_parent_id = "DOFU_PT_main_panel"

    def draw(self, context):
        dofu = context.scene.dof_utils #cam_ob = scene.camera.data
        cam_ob = context.active_object.data
        active_flag = not dofu.use_cursor and cam_ob.dof.focus_object is None

        layout = self.layout
        layout.use_property_split = True
        layout.active = cam_ob.dof.use_dof
        
        col = layout.column()
        col.prop(cam_ob.dof, "aperture_fstop", text="F-Stop")
        dis = col.column()
        dis.enabled = active_flag # active
        dis.prop(cam_ob.dof, "focus_distance", text="Focus Distance")
        col.prop(cam_ob, "lens")
        col.prop(cam_ob.dof, "focus_object", text="Focus Object")
        layout.separator()


class DOFU_PT_visualize(DOFU_panel, bpy.types.Panel):  
    bl_label = "Visualization"
    bl_parent_id = "DOFU_PT_main_panel"
    
    def draw(self, context):
        dofu = context.scene.dof_utils #cam_ob = scene.camera.data
        
        layout = self.layout
        layout.use_property_split = True
        layout.active = context.active_object.data.dof.use_dof

        col = layout.column()
        col.prop(dofu, "color_limits", text="Color")
        col.prop(dofu, "size_limits")
        col.prop(dofu, "opacity_limits")
        col.prop(dofu, "segments_limits")
        col = layout.column()
        col.prop(dofu, "overlay", text="Overlay Limits")#, toggle=True, icon="GHOST_ENABLED")
        col.prop(dofu, "draw_focus") #, toggle=True, icon="FORCE_FORCE")
        layout.separator()


# ------------------------------------------------------------------------
#    Registration
# ------------------------------------------------------------------------

classes = (
    DOFU_AP_preferences,
    DOFU_PG_settings,
    DOFU_OT_focusPicking,
    DOFU_OT_visualizeLimits,
    DOFU_OT_killVisualization,
    DOFU_OT_killFocusPicking,
    DOFU_OT_viewportReset,
    DOFU_OT_preferencesReset,
    DOFU_PT_main_panel,
    DOFU_PT_camera,
    DOFU_PT_visualize
)


def register():
    from bpy.utils import register_class
    for cls in classes:
        register_class(cls)

    bpy.types.Scene.dof_utils = bpy.props.PointerProperty(type=DOFU_PG_settings)

def unregister():
    bpy.ops.dof_utils.reset_preferences()
    
    from bpy.utils import unregister_class
    for cls in reversed(classes):
        unregister_class(cls)
    
    del bpy.types.Scene.dof_utils

if __name__ == "__main__":
    register()
