"""Rebuild tests/fixtures/joined_v7.blend with the 1.x add-on whose group is version 7.

The fixture is a joined-mode blend made, saved and evaluated by that release; it stores its own
evaluated result so tests can check that this version rebuilds it into the same geometry.

    git show b26810b:contact_blend.py > /some/dir/contact_blend_v7.py
    python -B tests/fixtures/make_joined_v7.py /some/dir tests/fixtures/joined_v7.blend
"""
import sys, bpy
sys.path.insert(0, sys.argv[-2])
import contact_blend_v7 as old
assert old.GROUP_VERSION == 7
bpy.ops.wm.read_factory_settings(use_empty=True)
old.register()
bpy.ops.mesh.primitive_grid_add(size=4, x_subdivisions=16, y_subdivisions=16)
ground = bpy.context.object; ground.name = "Ground"
gmat = bpy.data.materials.new("Ground"); old.ensure_node_tree(gmat); ground.data.materials.append(gmat)
bpy.ops.mesh.primitive_uv_sphere_add(segments=16, ring_count=8, radius=0.5, location=(0.13, -0.07, 0.3))
rock = bpy.context.object; rock.name = "Rock"
rmat = bpy.data.materials.new("Rock"); old.ensure_node_tree(rmat); rock.data.materials.append(rmat)
ground.select_set(True); bpy.context.view_layer.objects.active = rock
assert bpy.ops.object.contact_blend() == {"FINISHED"}
mod = old.find_mod(rock); ids = old.input_ids(mod.node_group)
mod[ids["Contact Subdivisions"]] = 2
mod[ids["Surface Offset"]] = 0.01
rock.update_tag()
me = rock.evaluated_get(bpy.context.evaluated_depsgraph_get()).to_mesh()
bpy.context.scene["cb_v7_positions"] = [c for v in me.vertices for c in v.co]
bpy.context.scene["cb_v7_blend"] = [d.value for d in me.attributes["blend"].data]
bpy.context.scene["cb_v7_normals"] = [c for n in me.corner_normals for c in n.vector]
print("verts", len(me.vertices), "group version", old.group_version(mod.node_group))
rock.evaluated_get(bpy.context.evaluated_depsgraph_get()).to_mesh_clear()
old.unregister()
bpy.ops.wm.save_as_mainfile(filepath=sys.argv[-1], compress=True)
