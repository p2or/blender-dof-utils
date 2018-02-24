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
    "description": "",
    "version": (0, 0, 6),
    "blender": (2, 77, 0),
    "location": "3d View > Properties Panel > Depth of Field Utilities",
    "wiki_url": "https://github.com/p2or/blender-dof-utils",
    "tracker_url": "https://github.com/p2or/blender-dof-utils/issues",
    "category": "Render"
}

import bpy
import bgl
import blf
import math
from mathutils import Matrix, Vector
from mathutils.geometry import intersect_point_line


# -------------------------------------------------------------------
#   Preferences & Scene Properties
# -------------------------------------------------------------------

class depthOfFieldUtilitiesPreferences(bpy.types.AddonPreferences):

    bl_idname = __name__
    
    display_info = bpy.props.BoolProperty(
            name="Display Infos in Viewport",
            default = True)
    
    display_limits = bpy.props.BoolProperty(
        name="Display Limits in Viewport Header",
        description="Displays distance, near & far limits in viewport header",
        default = True)
    
    def draw(self, context):
        layout = self.layout
        row = layout.row(align=True)
        row.prop(self, "display_info")
        row.prop(self, "display_limits")


class depthOfFieldUtilitiesSettings(bpy.types.PropertyGroup):

    use_cursor = bpy.props.BoolProperty(
        name="Use 3d Cursor Flag",
        description="",
        default=False,
        options={'SKIP_SAVE'})

    draw_dof = bpy.props.BoolProperty(
        name="Draw DoF Flag",
        description="",
        default=False,
        options={'SKIP_SAVE'})
    
    overlay = bpy.props.BoolProperty(
        name="Line overlay",
        description="Display DoF above all other Elements",
        default = True)
    
    limits = bpy.props.FloatVectorProperty(
            name="Limits",
            size=3)
    

# -------------------------------------------------------------------
#   Helper
# -------------------------------------------------------------------

def is_camera(obj):
    return obj is not None and obj.type == 'CAMERA'

def linear_distance(vector1, vector2):
    a, b = sorted((vector1, vector2), key=lambda v: v.z, reverse=True)
    return (b - a).length

def fstops(camera_data, magnification):
    """ Return or calculate f-stops (N) """
    if camera_data.cycles.aperture_type == 'RADIUS':
        #print("Radius:", ((cam.lens / cam.cycles.aperture_fstop) / 2000))
        if camera_data.cycles.aperture_size > 0.00001: # division by zero fix
            return ((camera_data.lens /1000 * magnification) / (camera_data.cycles.aperture_size *2))
        else:
            return camera_data.clip_end
    else:
        return camera_data.cycles.aperture_fstop
       
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


# -------------------------------------------------------------------
#   OpenGL callbacks
# -------------------------------------------------------------------

def draw_callback_3d(operator, context):

    scene = context.scene
    dofu = scene.dofutils

    if is_camera(context.object):
        cam_ob = context.object
    elif is_camera(scene.camera):
        cam_ob = scene.camera
    else:
        return

    mat = cam_ob.matrix_world    
    cam = cam_ob.data
    nmat = mat.normalized()
    target_scale = (1, 1, 1)
    smat = Matrix()
    for i in range(3):
        smat[i][i] = target_scale[i]
    temp_matrix = nmat * smat # cam_ob.matrix_world = nmat * smat
   
    start = temp_matrix * Vector((0, 0, -cam.clip_start))
    end = temp_matrix * Vector((0, 0, -cam.clip_end))
    d = cam.dof_distance

    if cam.dof_object is None:
        near_limit, far_limit = dof_calculation(cam, d)
        dof_loc = temp_matrix * Vector((0, 0, -(near_limit)))
        dof_loc_end = temp_matrix * Vector((0, 0, -(far_limit)))
        
    else:
        pt = cam.dof_object.matrix_world.translation
        loc = intersect_point_line(pt, temp_matrix.translation, temp_matrix * Vector((0, 0, -1)))      
        d = ((loc[0] - start).length) + cam.clip_start # respect the clipping start value
        
        near_limit, far_limit = dof_calculation(cam, d)
        dof_loc = temp_matrix * Vector((0, 0, -(near_limit)))
        dof_loc_end = temp_matrix * Vector((0, 0, -(far_limit)))
    
    dofu.limits = (d, near_limit, far_limit)

    # 80% alpha, 2 pixel width line
    bgl.glEnable(bgl.GL_BLEND)
    bgl.glEnable(bgl.GL_LINE_SMOOTH)
    bgl.glEnable(bgl.GL_DEPTH_TEST)
    
    # check overlay
    if dofu.overlay:
        bgl.glDisable(bgl.GL_DEPTH_TEST)
    else:
        bgl.glEnable(bgl.GL_DEPTH_TEST)
    
    # set line width
    bgl.glLineWidth(2)

    def line(color, start, end):
        bgl.glColor4f(*color)
        bgl.glBegin(bgl.GL_LINES)
        bgl.glVertex3f(*start)
        bgl.glVertex3f(*end)
        bgl.glEnd()
    
    # define the lines
    line((1.0, 1.0, 1.0, 0.1), dof_loc_end, end)
    line((1.0, 1.0, 1.0, 0.1), dof_loc, start)
    line((0.0, 1.0, 0.0, 0.8), dof_loc_end, dof_loc)

    # restore opengl defaults
    bgl.glLineWidth(1)
    bgl.glDisable(bgl.GL_BLEND)
    bgl.glDisable(bgl.GL_LINE_SMOOTH)
    bgl.glEnable(bgl.GL_DEPTH_TEST)
    bgl.glColor4f(0.0, 0.0, 0.0, 1.0)


def draw_string(x, y, packed_strings):
    font_id = 0
    blf.size(font_id, 17, 70) 
    x_offset = 0
    for pstr, pcol in packed_strings:
        bgl.glColor4f(*pcol)
        text_width, text_height = blf.dimensions(font_id, pstr)
        blf.position(font_id, (x + x_offset), y, 0)
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
 

# -------------------------------------------------------------------
#   Operators    
# -------------------------------------------------------------------

class killVisualization(bpy.types.Operator):
    """ Kill Visualization """
    bl_idname = "dofutils.kill_visualization"
    bl_label = "Kill Visualization"
    bl_description = "Kills Viewport Visualization"

    def execute(self, context):
        context.scene.dofutils.draw_dof = False
        return {'FINISHED'}


class killFocusPicking(bpy.types.Operator):
    """ Kill Focus Picking """
    bl_idname = "dofutils.kill_focus_picking"
    bl_label = "Kill Visualization"
    bl_description = "Kills Focus Picking"

    def execute(self, context):
        context.scene.dofutils.use_cursor = False
        return {'FINISHED'}


class focusPicking(bpy.types.Operator):
    """ Sets the focus distance by using the 3d cursor """
    bl_idname = "dofutils.focus_picking"
    bl_label = "Set Focus using 3d Cursor"
    bl_description = "Sets the focus distance by using the 3d cursor"
    bl_options = {'REGISTER', 'UNDO'}

    _handle_2d = None

    @classmethod
    def poll(cls, context):
        rd = context.scene.render
        return is_camera(context.object) and rd.engine == "CYCLES"

    def redraw_viewports(self, context):
        for area in context.screen.areas:
            if area.type == 'VIEW_3D':
                area.tag_redraw()

    def modal(self, context, event):
        context.area.tag_redraw()
        scene = context.scene
        dofu = scene.dofutils
        prefs = context.user_preferences.addons[__name__].preferences

        if event.type == 'LEFTMOUSE': 
            if event.value == 'RELEASE':
                context.object.data.dof_distance = \
                    linear_distance(scene.cursor_location, context.object.location)
            return {'PASS_THROUGH'}
        
        elif event.type in {'RIGHTMOUSE', 'ESC'} or not dofu.use_cursor:
            dofu.use_cursor = False
            if prefs.display_info:
                bpy.types.SpaceView3D.draw_handler_remove(self._handle_2d, 'WINDOW')
            self.redraw_viewports(context)
            return {'CANCELLED'}
        return {'PASS_THROUGH'}
    
    def invoke(self, context, event):
        dofu = context.scene.dofutils #context.area.tag_redraw()
        prefs = context.user_preferences.addons[__name__].preferences
        
        if not dofu.use_cursor:
            if context.space_data.type == 'VIEW_3D':
                if prefs.display_info:
                    args = (self, context)
                    self._handle_2d = bpy.types.SpaceView3D.draw_handler_add(draw_callback_2d, args, 'WINDOW', 'POST_PIXEL')
                
                context.window_manager.modal_handler_add(self)
                dofu.use_cursor = True
                return {'RUNNING_MODAL'}
            else:
                self.report({'WARNING'}, "View3D not found, cannot run operator")
                return {'CANCELLED'}
        else:
            self.report({'WARNING'}, "Operator is already running")
            return {'CANCELLED'}


class visualizeDepthOfField(bpy.types.Operator):
    """ Draws depth of field in the vieport via OpenGL """
    bl_idname = "dofutils.visualize_dof"
    bl_label = "Visualize Depth of Field"
    bl_description = "Draws depth of field in the vieport via OpenGL"

    _handle = None 
    _handle_2d = None

    @classmethod
    def poll(cls, context):
        rd = context.scene.render
        return is_camera(context.object) and rd.engine == "CYCLES"

    def redraw_viewports(self, context):
        for area in context.screen.areas:
            if area.type == 'VIEW_3D':
                area.tag_redraw()

    def modal(self, context, event):
        context.area.tag_redraw()
        dofu = context.scene.dofutils
        prefs = context.user_preferences.addons[__name__].preferences

        if prefs.display_limits:
            context.area.header_text_set("Focus Distance: %.3f Near Limit: %.3f Far Limit: %.3f" % tuple(dofu.limits))
        
        if event.type in {'RIGHTMOUSE', 'ESC'} or not dofu.draw_dof:
            dofu.draw_dof = False
            bpy.types.SpaceView3D.draw_handler_remove(self._handle, 'WINDOW')
            if prefs.display_info:
                bpy.types.SpaceView3D.draw_handler_remove(self._handle_2d, 'WINDOW')
            context.area.header_text_set()
            self.redraw_viewports(context)
            return {'CANCELLED'}
        
        return {'PASS_THROUGH'}
               
    def invoke(self, context, event):
        dofu = context.scene.dofutils
        prefs = context.user_preferences.addons[__name__].preferences

        if not dofu.draw_dof:
            if context.area.type == 'VIEW_3D':
                args = (self, context)
                # Add the region OpenGL drawing callback, draw in view space with 'POST_VIEW' and 'PRE_VIEW'
                self._handle = bpy.types.SpaceView3D.draw_handler_add(draw_callback_3d, args, 'WINDOW', 'POST_VIEW')
                if prefs.display_info:
                    self._handle_2d = bpy.types.SpaceView3D.draw_handler_add(draw_callback_2d, args, 'WINDOW', 'POST_PIXEL')
                
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


# -------------------------------------------------------------------
#   UI
# -------------------------------------------------------------------

class depthOfFieldUtilitiesPanel(bpy.types.Panel):  
    bl_label = "Depth of Field Utilities"  
    bl_space_type = "VIEW_3D"  
    bl_region_type = "UI"  
    
    @classmethod
    def poll(cls, context):
        rd = context.scene.render
        return is_camera(context.object) and rd.engine == "CYCLES"
    
    def draw(self, context):
        scene = context.scene
        dofu = scene.dofutils #cam_ob = scene.camera.data
        cam_ob = context.active_object.data
        ccam = cam_ob.cycles
        
        col = self.layout.column(align=True)
        row = col.row(align=True)
        viz = row.row(align=True)
        viz.enabled =  not dofu.draw_dof # enabled
        viz.operator("dofutils.visualize_dof", icon="SNAP_NORMAL" if not dofu.draw_dof else "REC")
        row = row.row(align=True)
        row.operator("dofutils.kill_visualization", icon="X", text="")
        row = col.row(align=True)
        row.prop(dofu, "overlay", text="Overlay Limits", toggle=True)

        col = self.layout.column(align=True)
        col.label("Aperture:")
        row = col.row(align=True)
        row.prop(ccam, "aperture_type", expand=True)
        if ccam.aperture_type == 'RADIUS':
            col.prop(ccam, "aperture_size", text="Size")
        elif ccam.aperture_type == 'FSTOP':
            col.prop(ccam, "aperture_fstop", text="Number")

        col = self.layout.column(align=True)
        col.label("Focus:")
        row = col.row(align=True)
        pic = row.row(align=True)
        active_flag = not dofu.use_cursor and cam_ob.dof_object is None
        pic.enabled = active_flag # enabled
        pic.operator("dofutils.focus_picking", icon="CURSOR" if active_flag else "REC")
        row = row.row(align=True) #layout.prop_search(dofu, "camera", bpy.data, "cameras")
        row.enabled = cam_ob.dof_object is None
        row.operator("dofutils.kill_focus_picking", icon="X", text="")
        row = col.row(align=True)
        pic = row.row(align=True)
        pic.enabled = active_flag # active
        pic.prop(cam_ob, "dof_distance", text="Distance")
        col.prop(cam_ob, "dof_object", text="")

        col = self.layout.column(align=True)
        cam_info = ["Name: {}".format(cam_ob.name)]
        if cam_ob.type == "PERSP":
            cam_info.append(" Lens: {:.2f}mm".format(cam_ob.lens))
        col.label(",".join(cam_info))
        #self.layout.separator()


# -------------------------------------------------------------------
#   Register
# -------------------------------------------------------------------

def register():
    bpy.utils.register_module(__name__)
    bpy.types.Scene.dofutils = bpy.props.PointerProperty(type=depthOfFieldUtilitiesSettings)
    #bpy.types.CyclesCamera_PT_dof.append(draw_dofutils)


def unregister():
    bpy.utils.unregister_module(__name__)
    del bpy.types.Scene.dofutils
    #bpy.types.CyclesCamera_PT_dof.remove(draw_dofutils)
    
if __name__ == "__main__":
    register()