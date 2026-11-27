extends SceneTree
## Isolated visual workload, not a race or sporting simulation.
var cars: Array[Node3D] = []

func _initialize() -> void:
    call_deferred("_run")

func _run() -> void:
    var args := OS.get_cmdline_user_args()
    if args.size() != 3:
        quit(2)
        return
    var mode: String = args[0]
    var profile: String = args[1]
    var samples: int = int(args[2])
    if mode not in ["headless", "software", "render"] or profile not in ["eco", "balanced"] or samples not in [120, 600, 1800]:
        quit(2)
        return
    Engine.max_fps = 0
    OS.low_processor_usage_mode = false
    var ground := MeshInstance3D.new()
    var ground_mesh := PlaneMesh.new()
    ground_mesh.size = Vector2(40, 45)
    ground.mesh = ground_mesh
    root.add_child(ground)
    var body := BoxMesh.new()
    body.size = Vector3(1.6, .55, 4.2)
    var tyre := CylinderMesh.new()
    tyre.top_radius = .31
    tyre.bottom_radius = .31
    tyre.height = .28
    tyre.radial_segments = 24
    var paint := StandardMaterial3D.new()
    paint.albedo_color = Color(.8,.15,.03)
    var rubber := StandardMaterial3D.new()
    rubber.albedo_color = Color(.035,.035,.035)
    var packed: PackedScene = null
    var asset := FileAccess.file_exists("res://asset.glb")
    if asset:
        packed = load("res://asset.glb") as PackedScene
        if packed == null:
            quit(3)
            return
    for i in range(24):
        var car := Node3D.new()
        root.add_child(car)
        cars.append(car)
        if packed != null:
            var model := packed.instantiate() as Node3D
            if model == null:
                quit(3)
                return
            car.add_child(model)
        else:
            var mesh := MeshInstance3D.new()
            mesh.mesh = body
            mesh.material_override = paint
            mesh.position.y = .55
            car.add_child(mesh)
            for x in [-.79,.79]:
                for z in [-1.3,1.25]:
                    var wheel := MeshInstance3D.new()
                    wheel.mesh = tyre
                    wheel.material_override = rubber
                    wheel.position = Vector3(x,.31,z)
                    wheel.rotation.z = PI / 2
                    car.add_child(wheel)
        car.position = Vector3((i % 6 - 2.5) * 4.8,0,(floori(i / 6.0) - 1.5) * 7.0)
    var cam := Camera3D.new()
    root.add_child(cam)
    cam.position = Vector3(23,30,29)
    cam.look_at(Vector3.ZERO)
    cam.projection = Camera3D.PROJECTION_ORTHOGONAL
    cam.size = 38
    cam.current = true
    var light := DirectionalLight3D.new()
    root.add_child(light)
    light.rotation_degrees = Vector3(-50,-30,0)
    light.light_energy = 1.4
    light.shadow_enabled = profile == "balanced"
    var env := WorldEnvironment.new()
    env.environment = Environment.new()
    env.environment.background_mode = Environment.BG_COLOR
    env.environment.background_color = Color(.12,.15,.19)
    env.environment.ambient_light_source = Environment.AMBIENT_SOURCE_COLOR
    env.environment.ambient_light_color = Color(.9,.9,.9)
    env.environment.ambient_light_energy = .6
    root.add_child(env)
    var times: Array[int] = []
    var previous := Time.get_ticks_usec()
    var draw_peak: int = 0
    for i in range(60 + samples):
        for j in range(cars.size()):
            cars[j].rotation.y = sin(float(i) * .03 + float(j)) * .12
        await process_frame
        if mode != "headless":
            await RenderingServer.frame_post_draw
        var stamp := Time.get_ticks_usec()
        if i >= 60:
            times.append(stamp - previous)
            if mode != "headless":
                draw_peak = maxi(draw_peak, RenderingServer.get_rendering_info(RenderingServer.RENDERING_INFO_TOTAL_DRAW_CALLS_IN_FRAME))
        previous = stamp
    print("APEX_BENCHMARK ", JSON.stringify({"schema":1,"mode":mode,"profile":profile,"cars":cars.size(),
        "samples":times,"warmup":60,"asset":asset,"viewport":[root.size.x,root.size.y],
        "adapter":RenderingServer.get_video_adapter_name(),"vendor":RenderingServer.get_video_adapter_vendor(),
        "renderer":RenderingServer.get_current_rendering_method(),"display":DisplayServer.get_name(),
        "draw_calls_peak":draw_peak}))
    quit(0)
