use evdev::{uinput::{VirtualDevice, VirtualDeviceBuilder}, AbsInfo, AbsoluteAxisType, BusType, InputId,
    AttributeSet, EventType, InputEvent, Key, PropType, RelativeAxisType, Synchronization, UinputAbsSetup};
use rusb::{Context, DeviceHandle, Direction, TransferType, UsbContext};
use signal_hook::{consts::{SIGINT, SIGTERM}, flag};
use std::{env, error::Error, fs::{self, OpenOptions}, io::{Seek, SeekFrom, Write}, sync::{Arc, Mutex, atomic::{AtomicBool, Ordering}, mpsc}, thread, time::{Duration, Instant}};

const VID: u16 = 0x08f2;
const PID: u16 = 0x6811;
const PROFILE_PATH: &str = "/dev/shm/t1161-active-profile";
const INPUT_PROFILE_PATH: &str = "/dev/shm/t1161-active-input-profile";
const CONTROL_ACTIONS_PATH: &str = "/dev/shm/t1161-control-actions";
// A area ativa da caneta e muito maior que a de um touchpad. Sem reduzir o
// deslocamento, pequenos movimentos viram gestos violentos nos aplicativos.
const TOUCH_MOTION_SCALE: f64 = 0.65;
// A T1161 oscila dezenas de unidades mesmo com a ponta parada. O touchpad
// virtual so deve nascer depois de um movimento deliberado; caso contrario a
// sequencia curta de contatos vira tap-to-click no libinput.
const TOUCH_ACTIVATION_DISTANCE: f64 = 320.0;
const FULL_MODE: [[u8; 8]; 4] = [
    [0x08, 0x04, 0x1d, 0x01, 0xff, 0xff, 0x06, 0x2e],
    [0x08, 0x03, 0x00, 0xff, 0xf0, 0x00, 0xff, 0xf0],
    [0x08, 0x06, 0x01, 0x00, 0x00, 0x00, 0x00, 0x00],
    [0x08, 0x03, 0x00, 0xff, 0xf0, 0x00, 0xff, 0xf0],
];

#[derive(Clone, Debug)]
struct ZoneMapping {
    tablet_x: f64,
    tablet_y: f64,
    tablet_width: f64,
    tablet_height: f64,
    desktop_x: f64,
    desktop_y: f64,
    desktop_width: f64,
    desktop_height: f64,
    rotation: u16,
}

#[derive(Clone, Debug)]
struct MappingConfig {
    desktop_x: f64,
    desktop_y: f64,
    desktop_width: f64,
    desktop_height: f64,
    zones: Vec<ZoneMapping>,
}

#[derive(Clone, Copy, Debug, PartialEq)]
enum Action { None, Left, Right, Middle }

#[derive(Clone, Copy, Debug, PartialEq)]
enum ControlAction { None, Left, Right, Middle, PenScroll, DualTouch, TripleTouch,
    MirrorZoom, ReverseExpand, ReverseContract }

#[derive(Clone, Copy, Debug)]
struct ControlActions {
    tip: ControlAction,
    pen04: ControlAction,
    pen06: ControlAction,
    tablet_left: u16,
    tablet_right: u16,
    tablet_middle: u16,
    tablet_scroll: u16,
    tablet_dual_touch: u16,
    tablet_triple_touch: u16,
    tablet_mirror_zoom: u16,
    tablet_reverse_expand: u16,
    tablet_reverse_contract: u16,
    tablet_menu: u16,
    touch_spacing: f64,
}

fn parse_control_action(value: &str) -> ControlAction {
    match value {
        "left" => ControlAction::Left,
        "right" => ControlAction::Right,
        "middle" => ControlAction::Middle,
        "pen-scroll" => ControlAction::PenScroll,
        "dual-touch" => ControlAction::DualTouch,
        "triple-touch" => ControlAction::TripleTouch,
        "mirror-zoom" => ControlAction::MirrorZoom,
        "reverse-expand" => ControlAction::ReverseExpand,
        "reverse-contract" => ControlAction::ReverseContract,
        _ => ControlAction::None,
    }
}

fn load_control_actions(profile: InputProfile) -> ControlActions {
    let fallback = |action| match action {
        Action::Left => ControlAction::Left, Action::Right => ControlAction::Right,
        Action::Middle => ControlAction::Middle, Action::None => ControlAction::None,
    };
    let mut config = ControlActions {
        tip: fallback(profile.pressure), pen04: fallback(profile.pen04), pen06: fallback(profile.pen06),
        tablet_left: 0, tablet_right: 0, tablet_middle: 0, tablet_scroll: 0,
        tablet_dual_touch: 0, tablet_triple_touch: 0, tablet_mirror_zoom: 0,
        tablet_reverse_expand: 0, tablet_reverse_contract: 0, tablet_menu: 0,
        touch_spacing: 180.0,
    };
    let Ok(text) = fs::read_to_string(CONTROL_ACTIONS_PATH) else { return config; };
    for line in text.lines() {
        let fields = line.split_whitespace().collect::<Vec<_>>();
        match fields.as_slice() {
            ["tip", value] => config.tip = parse_control_action(value),
            ["pen-04", value] => config.pen04 = parse_control_action(value),
            ["pen-06", value] => config.pen06 = parse_control_action(value),
            ["tablet-bit", bit, value] => {
                let Ok(bit) = bit.parse::<u16>() else { continue; };
                if bit >= 16 { continue; }
                let mask = 1u16 << bit;
                match parse_control_action(value) {
                    ControlAction::Left => config.tablet_left |= mask,
                    ControlAction::Right => config.tablet_right |= mask,
                    ControlAction::Middle => config.tablet_middle |= mask,
                    ControlAction::PenScroll => config.tablet_scroll |= mask,
                    ControlAction::DualTouch => config.tablet_dual_touch |= mask,
                    ControlAction::TripleTouch => config.tablet_triple_touch |= mask,
                    ControlAction::MirrorZoom => config.tablet_mirror_zoom |= mask,
                    ControlAction::ReverseExpand => config.tablet_reverse_expand |= mask,
                    ControlAction::ReverseContract => config.tablet_reverse_contract |= mask,
                    ControlAction::None => {}
                }
            }
            ["tablet-menu-bit", bit, value] if *value != "none" => {
                let Ok(bit) = bit.parse::<u16>() else { continue; };
                if bit < 16 { config.tablet_menu |= 1u16 << bit; }
            }
            ["touch-spacing", value] => config.touch_spacing = value.parse::<f64>()
                .unwrap_or(180.0).clamp(10.0, 500.0),
            _ => {}
        }
    }
    config
}

#[derive(Clone, Copy, Debug, PartialEq)]
enum Gesture { None, ScrollVertical, ScrollHorizontal }

#[derive(Clone, Copy, Debug)]
struct InputProfile {
    pressure: Action,
    pen04: Action,
    pen06: Action,
    gesture04: Gesture,
    scroll_speed: f64,
}

impl Default for InputProfile {
    fn default() -> Self {
        Self { pressure: Action::Left, pen04: Action::Right, pen06: Action::Middle,
            gesture04: Gesture::ScrollVertical, scroll_speed: 1.0 }
    }
}

fn parse_action(value: &str) -> Action {
    match value { "left" => Action::Left, "right" => Action::Right,
        "middle" => Action::Middle, _ => Action::None }
}

fn load_input_profile() -> InputProfile {
    let mut profile = InputProfile::default();
    let Ok(text) = fs::read_to_string(INPUT_PROFILE_PATH) else { return profile; };
    for line in text.lines() {
        let fields = line.split_whitespace().collect::<Vec<_>>();
        match fields.as_slice() {
            ["pressure", value] => profile.pressure = parse_action(value),
            ["pen04", value] => profile.pen04 = parse_action(value),
            ["pen06", value] => profile.pen06 = parse_action(value),
            ["gesture04", "scroll-vertical"] => profile.gesture04 = Gesture::ScrollVertical,
            ["gesture04", "scroll-horizontal"] => profile.gesture04 = Gesture::ScrollHorizontal,
            ["gesture04", _] => profile.gesture04 = Gesture::None,
            ["scroll_speed", value] => profile.scroll_speed = value.parse::<f64>().unwrap_or(1.0).clamp(0.1, 8.0),
            _ => {}
        }
    }
    profile
}

fn load_mapping() -> Option<MappingConfig> {
    let text = fs::read_to_string(PROFILE_PATH).ok()?;
    let mut desktop = None;
    let mut zones = Vec::new();
    for line in text.lines() {
        let fields = line.split_whitespace().collect::<Vec<_>>();
        match fields.as_slice() {
            ["desktop", x, y, width, height] => {
                desktop = Some((x.parse().ok()?, y.parse().ok()?, width.parse().ok()?, height.parse().ok()?));
            }
            ["zone", tx, ty, tw, th, dx, dy, dw, dh, rotation] => {
                let zone = ZoneMapping {
                    tablet_x: tx.parse().ok()?, tablet_y: ty.parse().ok()?,
                    tablet_width: tw.parse().ok()?, tablet_height: th.parse().ok()?,
                    desktop_x: dx.parse().ok()?, desktop_y: dy.parse().ok()?,
                    desktop_width: dw.parse().ok()?, desktop_height: dh.parse().ok()?,
                    rotation: rotation.parse().ok()?,
                };
                if zone.tablet_width > 0.0 && zone.tablet_height > 0.0
                    && zone.desktop_width > 0.0 && zone.desktop_height > 0.0
                    && matches!(zone.rotation, 0 | 90 | 180 | 270) {
                    zones.push(zone);
                }
            }
            _ => {}
        }
    }
    let (desktop_x, desktop_y, desktop_width, desktop_height) = desktop?;
    (desktop_width > 0.0 && desktop_height > 0.0).then_some(MappingConfig {
        desktop_x, desktop_y, desktop_width, desktop_height, zones,
    })
}

fn transform_point(config: &MappingConfig, raw_x: i32, raw_y: i32) -> Option<(i32, i32)> {
    let tablet_x = raw_x as f64 / 4095.0;
    let tablet_y = raw_y as f64 / 4095.0;
    let zone = config.zones.iter().find(|zone| {
        tablet_x >= zone.tablet_x && tablet_x <= zone.tablet_x + zone.tablet_width
            && tablet_y >= zone.tablet_y && tablet_y <= zone.tablet_y + zone.tablet_height
    })?;
    let u = ((tablet_x - zone.tablet_x) / zone.tablet_width).clamp(0.0, 1.0);
    let v = ((tablet_y - zone.tablet_y) / zone.tablet_height).clamp(0.0, 1.0);
    let (u, v) = match zone.rotation {
        90 => (1.0 - v, u),
        180 => (1.0 - u, 1.0 - v),
        270 => (v, 1.0 - u),
        _ => (u, v),
    };
    let desktop_x = zone.desktop_x + u * zone.desktop_width;
    let desktop_y = zone.desktop_y + v * zone.desktop_height;
    let output_x = ((desktop_x - config.desktop_x) / config.desktop_width * 4095.0).round();
    let output_y = ((desktop_y - config.desktop_y) / config.desktop_height * 4095.0).round();
    Some((output_x.clamp(0.0, 4095.0) as i32, output_y.clamp(0.0, 4095.0) as i32))
}

fn open_usb() -> Result<(DeviceHandle<Context>, u8, Option<u8>), Box<dyn Error>> {
    let context = Context::new()?;
    let device = context.devices()?.iter().find(|device| device.device_descriptor()
        .map(|d| d.vendor_id() == VID && d.product_id() == PID).unwrap_or(false))
        .ok_or("T1161 não encontrada")?;
    let mut handle = device.open()?;
    handle.set_auto_detach_kernel_driver(true)?;
    let config = device.active_config_descriptor()?;
    let mut endpoint = None;
    let mut auxiliary_endpoint = None;
    for interface in config.interfaces() {
        for descriptor in interface.descriptors() {
            if descriptor.class_code() != 3 { continue; }
            let number = descriptor.interface_number();
            if handle.claim_interface(number).is_err() { continue; }
            for ep in descriptor.endpoint_descriptors() {
                if ep.direction() != Direction::In || ep.transfer_type() != TransferType::Interrupt {
                    continue;
                }
                match ep.max_packet_size() {
                    64 => endpoint = Some(ep.address()),
                    8 => auxiliary_endpoint = Some(ep.address()),
                    _ => {}
                }
            }
        }
    }
    for report in FULL_MODE { handle.write_control(0x21, 0x09, 0x0308, 2, &report, Duration::from_millis(500))?; }
    Ok((handle, endpoint.ok_or("endpoint de 64 bytes não encontrado")?, auxiliary_endpoint))
}

fn virtual_pen() -> Result<VirtualDevice, std::io::Error> {
    // Resoluções diferentes descrevem a área física 300 × 188 mm sem deformá-la.
    let x = UinputAbsSetup::new(AbsoluteAxisType::ABS_X, AbsInfo::new(0, 0, 4095, 0, 0, 14));
    let y = UinputAbsSetup::new(AbsoluteAxisType::ABS_Y, AbsInfo::new(0, 0, 4095, 0, 0, 22));
    let p = UinputAbsSetup::new(AbsoluteAxisType::ABS_PRESSURE, AbsInfo::new(0, 0, 8191, 0, 0, 1));
    let d = UinputAbsSetup::new(AbsoluteAxisType::ABS_DISTANCE, AbsInfo::new(0, 0, 255, 0, 0, 1));
    let mut keys = AttributeSet::<Key>::new();
    for key in [Key::BTN_TOOL_PEN, Key::BTN_TOUCH, Key::BTN_STYLUS, Key::BTN_STYLUS2] {
        keys.insert(key);
    }
    VirtualDeviceBuilder::new()?.name("T1161 Control Pen")
        .input_id(InputId::new(BusType::BUS_USB, VID, PID, 1))
        .with_absolute_axis(&x)?.with_absolute_axis(&y)?.with_absolute_axis(&p)?.with_absolute_axis(&d)?
        .with_keys(&keys)?.build()
}

fn virtual_buttons() -> Result<VirtualDevice, std::io::Error> {
    // Os cliques vivem em um ponteiro absoluto proprio. Cada quadro leva a
    // posicao da caneta antes do botao, portanto nunca usa a ultima posicao do
    // mouse fisico.
    let mut keys = AttributeSet::<Key>::new();
    for key in [Key::BTN_LEFT, Key::BTN_RIGHT, Key::BTN_MIDDLE] { keys.insert(key); }
    let x = UinputAbsSetup::new(AbsoluteAxisType::ABS_X, AbsInfo::new(0, 0, 4095, 0, 0, 1));
    let y = UinputAbsSetup::new(AbsoluteAxisType::ABS_Y, AbsInfo::new(0, 0, 4095, 0, 0, 1));
    let mut properties = AttributeSet::<PropType>::new();
    properties.insert(PropType::POINTER);
    VirtualDeviceBuilder::new()?.name("T1161 Control Buttons")
        .input_id(InputId::new(BusType::BUS_USB, VID, PID, 2))
        .with_keys(&keys)?.with_properties(&properties)?
        .with_absolute_axis(&x)?.with_absolute_axis(&y)?.build()
}

fn virtual_scroll() -> Result<VirtualDevice, std::io::Error> {
    // A rolagem relativa precisa ficar separada dos eixos absolutos da
    // caneta. Dispositivos híbridos ABS + REL podem fazer o compositor
    // suspender o movimento do cursor enquanto um gesto está ativo.
    let mut relative = AttributeSet::<RelativeAxisType>::new();
    relative.insert(RelativeAxisType::REL_WHEEL);
    relative.insert(RelativeAxisType::REL_HWHEEL);
    VirtualDeviceBuilder::new()?.name("T1161 Control Scroll")
        .input_id(InputId::new(BusType::BUS_USB, VID, PID, 3))
        .with_relative_axes(&relative)?.build()
}

fn virtual_touchpad() -> Result<VirtualDevice, std::io::Error> {
    let axis = |kind, minimum, maximum, resolution| UinputAbsSetup::new(
        kind, AbsInfo::new(0, minimum, maximum, 0, 0, resolution));
    let mut keys = AttributeSet::<Key>::new();
    for key in [Key::BTN_TOUCH, Key::BTN_TOOL_FINGER, Key::BTN_TOOL_DOUBLETAP,
        Key::BTN_TOOL_TRIPLETAP] {
        keys.insert(key);
    }
    let mut properties = AttributeSet::<PropType>::new();
    // POINTER caracteriza uma superficie indireta. DIRECT faria o libinput
    // tratar os contatos como touchscreen e repassa-los sem reconhecer
    // rolagem, swipe ou pinch.
    properties.insert(PropType::POINTER);
    VirtualDeviceBuilder::new()?.name("T1161 Control Touchpad")
        .input_id(InputId::new(BusType::BUS_USB, VID, PID, 4))
        .with_keys(&keys)?.with_properties(&properties)?
        // 40/64 unidades por mm descrevem uma superficie indireta de
        // aproximadamente 102 x 64 mm, proporcao comum de touchpads.
        .with_absolute_axis(&axis(AbsoluteAxisType::ABS_X, 0, 4095, 40))?
        .with_absolute_axis(&axis(AbsoluteAxisType::ABS_Y, 0, 4095, 64))?
        .with_absolute_axis(&axis(AbsoluteAxisType::ABS_MT_SLOT, 0, 2, 0))?
        .with_absolute_axis(&axis(AbsoluteAxisType::ABS_MT_TRACKING_ID, 0, 65535, 0))?
        .with_absolute_axis(&axis(AbsoluteAxisType::ABS_MT_POSITION_X, 0, 4095, 40))?
        .with_absolute_axis(&axis(AbsoluteAxisType::ABS_MT_POSITION_Y, 0, 4095, 64))?
        .build()
}

#[derive(Clone, Copy, Debug, PartialEq)]
enum TouchMode { None, Parallel2, Parallel3, Mirror, ReverseExpand, ReverseContract }

struct TouchGesture {
    mode: TouchMode,
    origin: (i32, i32),
    activated: bool,
    contacts: usize,
}

impl Default for TouchGesture {
    fn default() -> Self {
        Self { mode: TouchMode::None, origin: (0, 0), activated: false, contacts: 0 }
    }
}

fn touch_positions(gesture: &mut TouchGesture, mode: TouchMode, x: i32, y: i32,
        spacing: f64) -> Vec<(i32, i32)> {
    if gesture.mode != mode {
        gesture.mode = mode;
        gesture.origin = (x, y);
        gesture.activated = false;
    }
    let dx = (x - gesture.origin.0) as f64;
    let dy = (y - gesture.origin.1) as f64;
    let distance = dx.hypot(dy);
    // Scroll e swipe exigem que todos os dedos mantenham a mesma distancia e
    // orientacao. Girar a linha dos contatos conforme o movimento parece um
    // pinch/rotate para o libinput, sobretudo ao mover horizontalmente.
    let normal = (1.0, 0.0);
    let clamp = |value: f64| value.round().clamp(0.0, 4095.0) as i32;
    let offset = |center: (i32, i32), amount: f64| (
        clamp(center.0 as f64 + normal.0 * amount),
        clamp(center.1 as f64 + normal.1 * amount));
    let scaled_center = (
        clamp(gesture.origin.0 as f64 + dx * TOUCH_MOTION_SCALE),
        clamp(gesture.origin.1 as f64 + dy * TOUCH_MOTION_SCALE),
    );
    match mode {
        TouchMode::Parallel2 | TouchMode::Parallel3
            if !gesture.activated && distance < TOUCH_ACTIVATION_DISTANCE => Vec::new(),
        TouchMode::Parallel2 => {
            gesture.activated = true;
            vec![offset(scaled_center, -spacing), offset(scaled_center, spacing)]
        }
        TouchMode::Parallel3 => {
            gesture.activated = true;
            vec![offset(scaled_center, -spacing), scaled_center, offset(scaled_center, spacing)]
        }
        TouchMode::Mirror => {
            let spread = distance.max(8.0);
            let ux = if distance > 0.0 { dx / distance } else { 1.0 };
            let uy = if distance > 0.0 { dy / distance } else { 0.0 };
            vec![(clamp(gesture.origin.0 as f64 + ux * spread), clamp(gesture.origin.1 as f64 + uy * spread)),
                 (clamp(gesture.origin.0 as f64 - ux * spread), clamp(gesture.origin.1 as f64 - uy * spread))]
        }
        TouchMode::ReverseExpand | TouchMode::ReverseContract => {
            let signed = if mode == TouchMode::ReverseExpand { distance } else { -distance };
            let radius = (spacing + signed).clamp(5.0, 1200.0);
            vec![offset(gesture.origin, -radius), offset(gesture.origin, radius)]
        }
        TouchMode::None => Vec::new(),
    }
}

fn emit_touch_gesture(device: &mut VirtualDevice, x: i32, y: i32, _pressure: i32,
        mode: TouchMode, gesture: &mut TouchGesture, spacing: f64) -> std::io::Result<()> {
    let positions = touch_positions(gesture, mode, x, y, spacing);
    let mut old_contacts = gesture.contacts;
    // Ao iniciar ja em uma posicao deslocada, publique primeiro a origem dos
    // dedos. O quadro seguinte contem movimento suficiente para o libinput
    // classificar a sequencia como gesto, nunca como tap-to-click.
    if old_contacts == 0 && !positions.is_empty()
        && matches!(mode, TouchMode::Parallel2 | TouchMode::Parallel3) {
        let count = positions.len();
        let origin = gesture.origin;
        let origin_positions = if count == 2 {
            vec![(origin.0 - spacing.round() as i32, origin.1),
                 (origin.0 + spacing.round() as i32, origin.1)]
        } else {
            vec![(origin.0 - spacing.round() as i32, origin.1), origin,
                 (origin.0 + spacing.round() as i32, origin.1)]
        };
        let mut start = vec![
            InputEvent::new(EventType::KEY, Key::BTN_TOUCH.code(), 1),
            InputEvent::new(EventType::KEY, Key::BTN_TOOL_FINGER.code(), 0),
            InputEvent::new(EventType::KEY, Key::BTN_TOOL_DOUBLETAP.code(), (count == 2) as i32),
            InputEvent::new(EventType::KEY, Key::BTN_TOOL_TRIPLETAP.code(), (count == 3) as i32),
            InputEvent::new(EventType::ABSOLUTE, AbsoluteAxisType::ABS_X.0, origin.0),
            InputEvent::new(EventType::ABSOLUTE, AbsoluteAxisType::ABS_Y.0, origin.1),
        ];
        for (slot, position) in origin_positions.iter().enumerate() {
            start.push(InputEvent::new(EventType::ABSOLUTE,
                AbsoluteAxisType::ABS_MT_SLOT.0, slot as i32));
            start.push(InputEvent::new(EventType::ABSOLUTE,
                AbsoluteAxisType::ABS_MT_TRACKING_ID.0, slot as i32 + 1));
            start.push(InputEvent::new(EventType::ABSOLUTE,
                AbsoluteAxisType::ABS_MT_POSITION_X.0, position.0.clamp(0, 4095)));
            start.push(InputEvent::new(EventType::ABSOLUTE,
                AbsoluteAxisType::ABS_MT_POSITION_Y.0, position.1.clamp(0, 4095)));
        }
        start.push(InputEvent::new(EventType::SYNCHRONIZATION,
            Synchronization::SYN_REPORT.0, 0));
        device.emit(&start)?;
        old_contacts = count;
    }
    let mut events = Vec::new();
    if !positions.is_empty() {
        events.push(InputEvent::new(EventType::KEY, Key::BTN_TOUCH.code(), 1));
        events.push(InputEvent::new(EventType::KEY, Key::BTN_TOOL_FINGER.code(),
            (positions.len() == 1) as i32));
        events.push(InputEvent::new(EventType::KEY, Key::BTN_TOOL_DOUBLETAP.code(),
            (positions.len() == 2) as i32));
        events.push(InputEvent::new(EventType::KEY, Key::BTN_TOOL_TRIPLETAP.code(),
            (positions.len() == 3) as i32));
        events.push(InputEvent::new(EventType::ABSOLUTE, AbsoluteAxisType::ABS_X.0, x));
        events.push(InputEvent::new(EventType::ABSOLUTE, AbsoluteAxisType::ABS_Y.0, y));
        for (slot, position) in positions.iter().enumerate() {
            events.push(InputEvent::new(EventType::ABSOLUTE, AbsoluteAxisType::ABS_MT_SLOT.0, slot as i32));
            if slot >= old_contacts {
                events.push(InputEvent::new(EventType::ABSOLUTE,
                    AbsoluteAxisType::ABS_MT_TRACKING_ID.0, slot as i32 + 1));
            }
            events.push(InputEvent::new(EventType::ABSOLUTE,
                AbsoluteAxisType::ABS_MT_POSITION_X.0, position.0));
            events.push(InputEvent::new(EventType::ABSOLUTE,
                AbsoluteAxisType::ABS_MT_POSITION_Y.0, position.1));
        }
        for slot in positions.len()..old_contacts {
            events.push(InputEvent::new(EventType::ABSOLUTE, AbsoluteAxisType::ABS_MT_SLOT.0, slot as i32));
            events.push(InputEvent::new(EventType::ABSOLUTE,
                AbsoluteAxisType::ABS_MT_TRACKING_ID.0, -1));
        }
    } else if old_contacts > 0 {
        for slot in 0..old_contacts {
            events.push(InputEvent::new(EventType::ABSOLUTE, AbsoluteAxisType::ABS_MT_SLOT.0, slot as i32));
            events.push(InputEvent::new(EventType::ABSOLUTE, AbsoluteAxisType::ABS_MT_TRACKING_ID.0, -1));
        }
        events.push(InputEvent::new(EventType::KEY, Key::BTN_TOUCH.code(), 0));
        events.push(InputEvent::new(EventType::KEY, Key::BTN_TOOL_FINGER.code(), 0));
        events.push(InputEvent::new(EventType::KEY, Key::BTN_TOOL_DOUBLETAP.code(), 0));
        events.push(InputEvent::new(EventType::KEY, Key::BTN_TOOL_TRIPLETAP.code(), 0));
    }
    if !events.is_empty() {
        events.push(InputEvent::new(EventType::SYNCHRONIZATION, Synchronization::SYN_REPORT.0, 0));
        device.emit(&events)?;
    }
    gesture.contacts = positions.len();
    if positions.is_empty() && mode == TouchMode::None {
        gesture.mode = TouchMode::None;
        gesture.activated = false;
    }
    Ok(())
}

fn emit(device: &mut VirtualDevice, x: i32, y: i32, pressure: i32, touching: bool,
        present: bool, distance: i32) -> std::io::Result<()> {
    device.emit(&[
        InputEvent::new(EventType::KEY, Key::BTN_TOOL_PEN.code(), present as i32),
        InputEvent::new(EventType::KEY, Key::BTN_TOUCH.code(), (touching && present) as i32),
        InputEvent::new(EventType::ABSOLUTE, AbsoluteAxisType::ABS_X.0, x),
        InputEvent::new(EventType::ABSOLUTE, AbsoluteAxisType::ABS_Y.0, y),
        InputEvent::new(EventType::ABSOLUTE, AbsoluteAxisType::ABS_PRESSURE.0, pressure),
        InputEvent::new(EventType::ABSOLUTE, AbsoluteAxisType::ABS_DISTANCE.0, if present { distance } else { 0 }),
        InputEvent::new(EventType::SYNCHRONIZATION, Synchronization::SYN_REPORT.0, 0),
    ])
}

fn emit_buttons(device: &mut VirtualDevice, x: i32, y: i32,
        buttons: (bool, bool, bool)) -> std::io::Result<()> {
    device.emit(&[
        InputEvent::new(EventType::ABSOLUTE, AbsoluteAxisType::ABS_X.0, x),
        InputEvent::new(EventType::ABSOLUTE, AbsoluteAxisType::ABS_Y.0, y),
        InputEvent::new(EventType::KEY, Key::BTN_LEFT.code(), buttons.0 as i32),
        InputEvent::new(EventType::KEY, Key::BTN_RIGHT.code(), buttons.1 as i32),
        InputEvent::new(EventType::KEY, Key::BTN_MIDDLE.code(), buttons.2 as i32),
        InputEvent::new(EventType::SYNCHRONIZATION, Synchronization::SYN_REPORT.0, 0),
    ])
}

fn emit_scroll(device: &mut VirtualDevice, wheel: (i32, i32)) -> std::io::Result<()> {
    if wheel == (0, 0) { return Ok(()); }
    device.emit(&[
        InputEvent::new(EventType::RELATIVE, RelativeAxisType::REL_WHEEL.0, wheel.0),
        InputEvent::new(EventType::RELATIVE, RelativeAxisType::REL_HWHEEL.0, wheel.1),
        InputEvent::new(EventType::SYNCHRONIZATION, Synchronization::SYN_REPORT.0, 0),
    ])
}

fn control_buttons(config: ControlActions, touching: bool, pen04: bool, pen06: bool,
        tablet_changed: u16) -> (bool, bool, bool) {
    // O clique primario da ponta ja e BTN_TOUCH no proprio dispositivo da
    // caneta. Repeti-lo como BTN_LEFT no dispositivo de mouse faz o compositor
    // alternar entre o cursor do mouse e o da caneta (e, portanto, entre duas
    // posicoes) a cada toque.
    let tip_as_mouse_button = touching && config.tip != ControlAction::Left;
    let active = [(config.tip, tip_as_mouse_button), (config.pen04, pen04), (config.pen06, pen06)];
    (active.iter().any(|item| *item == (ControlAction::Left, true))
        || tablet_changed & config.tablet_left != 0,
     active.iter().any(|item| *item == (ControlAction::Right, true))
        || tablet_changed & config.tablet_right != 0,
     active.iter().any(|item| *item == (ControlAction::Middle, true))
        || tablet_changed & config.tablet_middle != 0)
}

fn action_touch_mode(action: ControlAction) -> TouchMode {
    match action {
        ControlAction::DualTouch => TouchMode::Parallel2,
        ControlAction::TripleTouch => TouchMode::Parallel3,
        ControlAction::MirrorZoom => TouchMode::Mirror,
        ControlAction::ReverseExpand => TouchMode::ReverseExpand,
        ControlAction::ReverseContract => TouchMode::ReverseContract,
        _ => TouchMode::None,
    }
}

fn selected_touch_mode(config: ControlActions, tablet: u16, pen04: bool, pen06: bool) -> TouchMode {
    let candidates = [
        (config.tablet_reverse_contract, TouchMode::ReverseContract),
        (config.tablet_reverse_expand, TouchMode::ReverseExpand),
        (config.tablet_mirror_zoom, TouchMode::Mirror),
        (config.tablet_triple_touch, TouchMode::Parallel3),
        (config.tablet_dual_touch, TouchMode::Parallel2),
    ];
    for (mask, mode) in candidates {
        if tablet & mask != 0 { return mode; }
    }
    let pen_mode = if pen04 { action_touch_mode(config.pen04) }
        else if pen06 { action_touch_mode(config.pen06) } else { TouchMode::None };
    pen_mode
}

#[derive(Default)]
struct Pen04GestureState {
    was_pressed: bool,
    gesture_used: bool,
}

impl Pen04GestureState {
    fn update(&mut self, pressed: bool, touching: bool, gesture: Gesture) -> (bool, bool) {
        let gesture_active = pressed && touching && gesture != Gesture::None;
        if gesture_active {
            self.gesture_used = true;
        }
        let isolated_click = self.was_pressed && !pressed && !self.gesture_used
            && gesture != Gesture::None;
        if !pressed {
            self.gesture_used = false;
        }
        self.was_pressed = pressed;
        (gesture_active, isolated_click)
    }
}

fn idle_timeout() -> Option<Duration> {
    let seconds = fs::read_to_string("/etc/t1161-control/idle-timeout")
        .ok()
        .and_then(|value| value.trim().parse::<u64>().ok())
        .unwrap_or(0);
    (seconds > 0).then(|| Duration::from_secs(seconds.clamp(1, 60)))
}

fn presence_settings() -> (u8, i32) {
    let values = fs::read_to_string("/etc/t1161-control/presence-settings")
        .unwrap_or_else(|_| "2 1550".into())
        .split_whitespace()
        .filter_map(|value| value.parse::<i32>().ok())
        .collect::<Vec<_>>();
    let mode = values.first().copied().unwrap_or(2).clamp(0, 2) as u8;
    let threshold = values.get(1).copied().unwrap_or(1550).clamp(1, 3000);
    (mode, threshold)
}

fn pressure_settings() -> (i32, i32, u8, u8) {
    let values = fs::read_to_string("/etc/t1161-control/pressure-settings")
        .unwrap_or_default()
        .split_whitespace()
        .filter_map(|value| value.parse::<i32>().ok())
        .collect::<Vec<_>>();
    // Formato novo: ponto de clique, ponto de força máxima, confirmação do
    // toque e confirmação da soltura. Aceita o formato antigo de três valores.
    let click = values.first().copied().unwrap_or(1490).clamp(1, 2000);
    let (maximum, press_index, release_index) = if values.len() >= 4 {
        (values[1].clamp(0, click - 1), 2, 3)
    } else {
        (0, 1, 2)
    };
    let press = values.get(press_index).copied().unwrap_or(2).clamp(1, 10) as u8;
    let release = values.get(release_index).copied().unwrap_or(3).clamp(1, 10) as u8;
    (click, maximum, press, release)
}

fn map_pressure(raw: i32, click: i32, maximum: i32) -> i32 {
    if raw > click {
        return 0;
    }
    let span = (click - maximum).max(1);
    (((click - raw).max(0) * 8191) / span).clamp(1, 8191)
}

#[cfg(test)]
mod tests {
    use super::{control_buttons, map_pressure, transform_point, ControlAction, ControlActions,
        Gesture, MappingConfig, Pen04GestureState, touch_positions, TouchGesture, TouchMode,
        ZoneMapping};

    fn button_config(tip: ControlAction) -> ControlActions {
        ControlActions {
            tip,
            pen04: ControlAction::Right,
            pen06: ControlAction::Middle,
            tablet_left: 0,
            tablet_right: 0,
            tablet_middle: 0,
            tablet_scroll: 0,
            tablet_dual_touch: 0,
            tablet_triple_touch: 0,
            tablet_mirror_zoom: 0,
            tablet_reverse_expand: 0,
            tablet_reverse_contract: 0,
            tablet_menu: 0,
            touch_spacing: 180.0,
        }
    }

    #[test]
    fn calibrated_pressure_uses_the_whole_range() {
        assert_eq!(map_pressure(1601, 1600, 200), 0);
        assert_eq!(map_pressure(1600, 1600, 200), 1);
        assert_eq!(map_pressure(900, 1600, 200), 4095);
        assert_eq!(map_pressure(200, 1600, 200), 8191);
        assert_eq!(map_pressure(0, 1600, 200), 8191);
    }

    #[test]
    fn primary_pen_tip_is_not_duplicated_as_a_mouse_click() {
        assert_eq!(control_buttons(button_config(ControlAction::Left), true, false, false, 0),
            (false, false, false));
        assert_eq!(control_buttons(button_config(ControlAction::Right), true, false, false, 0),
            (false, true, false));
    }

    fn config(rotation: u16) -> MappingConfig {
        MappingConfig {
            desktop_x: 0.0, desktop_y: 0.0, desktop_width: 2000.0, desktop_height: 1000.0,
            zones: vec![ZoneMapping {
                tablet_x: 0.0, tablet_y: 0.0, tablet_width: 0.5, tablet_height: 1.0,
                desktop_x: 1000.0, desktop_y: 0.0, desktop_width: 1000.0, desktop_height: 1000.0,
                rotation,
            }],
        }
    }

    #[test]
    fn ignores_points_outside_every_zone() {
        assert_eq!(transform_point(&config(0), 4095, 2048), None);
    }

    #[test]
    fn maps_zone_to_its_desktop_region() {
        assert_eq!(transform_point(&config(0), 0, 0), Some((2048, 0)));
        assert_eq!(transform_point(&config(0), 2047, 4095), Some((4095, 4095)));
    }

    #[test]
    fn quarter_turn_rotates_real_movement() {
        assert_eq!(transform_point(&config(90), 0, 0), Some((4095, 0)));
        assert_eq!(transform_point(&config(90), 2047, 0), Some((4095, 4094)));
    }

    #[test]
    fn pen04_isolated_press_becomes_click_only_on_release() {
        let mut state = Pen04GestureState::default();
        assert_eq!(state.update(true, false, Gesture::ScrollVertical), (false, false));
        assert_eq!(state.update(false, false, Gesture::ScrollVertical), (false, true));
    }

    #[test]
    fn pen04_pressure_movement_does_not_leak_an_isolated_click() {
        let mut state = Pen04GestureState::default();
        assert_eq!(state.update(true, false, Gesture::ScrollVertical), (false, false));
        assert_eq!(state.update(true, true, Gesture::ScrollVertical), (true, false));
        assert_eq!(state.update(false, false, Gesture::ScrollVertical), (false, false));
    }

    #[test]
    fn parallel_contacts_keep_fixed_spacing_and_orientation() {
        let mut gesture = TouchGesture::default();
        let initial = touch_positions(&mut gesture, TouchMode::Parallel2, 1000, 1000, 55.0);
        assert!(initial.is_empty());
        assert!(touch_positions(&mut gesture, TouchMode::Parallel2,
            1000, 1100, 55.0).is_empty());
        let downward = touch_positions(&mut gesture, TouchMode::Parallel2, 1000, 1400, 55.0);
        assert_eq!(downward, vec![(945, 1260), (1055, 1260)]);
        let rightward = touch_positions(&mut gesture, TouchMode::Parallel2, 1400, 1400, 55.0);
        assert_eq!(rightward, vec![(1205, 1260), (1315, 1260)]);
        let mut triple_gesture = TouchGesture::default();
        assert!(touch_positions(&mut triple_gesture, TouchMode::Parallel3,
            1000, 1000, 55.0).is_empty());
        let triple = touch_positions(&mut triple_gesture, TouchMode::Parallel3, 1400, 1000, 55.0);
        assert_eq!(triple, vec![(1205, 1000), (1260, 1000), (1315, 1000)]);
    }

    #[test]
    fn mirror_contacts_keep_the_first_touch_as_center() {
        let mut gesture = TouchGesture::default();
        let _ = touch_positions(&mut gesture, TouchMode::Mirror, 1000, 1000, 55.0);
        assert_eq!(touch_positions(&mut gesture, TouchMode::Mirror, 1100, 1040, 55.0),
            vec![(1100, 1040), (900, 960)]);
    }
}

fn main() -> Result<(), Box<dyn Error>> {
    let stop = Arc::new(AtomicBool::new(false));
    flag::register(SIGINT, stop.clone())?;
    flag::register(SIGTERM, stop.clone())?;
    let (handle, endpoint, auxiliary_endpoint) = open_usb()?;
    let handle = Arc::new(handle);
    let mut pen = virtual_pen()?;
    let mut button_device = virtual_buttons()?;
    let mut scroll = virtual_scroll()?;
    let mut touchpad = virtual_touchpad()?;
    std::fs::create_dir_all("/run/t1161-control")?;
    let mut telemetry = OpenOptions::new().create(true).write(true).truncate(true)
        .open("/run/t1161-control/state")?;
    let mut pointer_state = OpenOptions::new().create(true).write(true).truncate(true)
        .open("/run/t1161-control/pointer-position")?;
    let test_seconds = env::args().skip_while(|arg| arg != "--test-seconds").nth(1)
        .and_then(|value| value.parse::<u64>().ok());
    let deadline = test_seconds.map(|seconds| Instant::now() + Duration::from_secs(seconds));
    let mut touching = false;
    let mut press_frames = 0u8;
    let mut release_frames = 0u8;
    let mut last_sample: Option<(i32, i32, i32, u8, u16)> = None;
    let mut last_change = Instant::now();
    let mut present = true;
    let mut timeout = idle_timeout();
    let (mut presence_mode, mut presence_threshold) = presence_settings();
    let (mut pressure_threshold, mut pressure_maximum, mut press_required, mut release_required) = pressure_settings();
    let mut next_config_read = Instant::now() + Duration::from_secs(1);
    let mut last_emit = Instant::now();
    let mut mapping = load_mapping();
    let mut input_profile = load_input_profile();
    let mut control_actions = load_control_actions(input_profile);
    let mut tablet_baseline: Option<u16> = None;
    let mut touch_gesture = TouchGesture::default();
    let mut menu_visible = false;
    let mut next_menu_read = Instant::now();
    let mut scroll_anchor: Option<(i32, i32)> = None;
    let mut scroll_remainder = (0.0f64, 0.0f64);
    let mut pen04_gesture = Pen04GestureState::default();
    let mut inside_mapping = false;
    let mut last_mapped_position: Option<(i32, i32)> = None;
    let (usb_tx, usb_rx) = mpsc::channel();
    let usb_stop = stop.clone();
    let main_handle = handle.clone();
    thread::spawn(move || {
        let mut usb_data = [0u8; 64];
        while !usb_stop.load(Ordering::Relaxed) {
            match main_handle.read_interrupt(endpoint, &mut usb_data, Duration::from_millis(100)) {
                Ok(size) => {
                    if usb_tx.send(Ok((size, usb_data))).is_err() { break; }
                }
                Err(rusb::Error::Timeout) => {}
                Err(error) => {
                    let _ = usb_tx.send(Err(error));
                    break;
                }
            }
        }
    });
    // O endpoint HID curto (0x85 nesta T1161) não participa do controle do
    // ponteiro, mas é amostrado em paralelo para diagnosticar se o firmware
    // continua enviando movimento quando o relatório principal congela.
    let auxiliary_state = Arc::new(Mutex::new((0u64, 0usize, [0u8; 8], Instant::now())));
    if let Some(aux_endpoint) = auxiliary_endpoint {
        let aux_handle = handle.clone();
        let aux_stop = stop.clone();
        let aux_state = auxiliary_state.clone();
        thread::spawn(move || {
            let mut data = [0u8; 8];
            while !aux_stop.load(Ordering::Relaxed) {
                match aux_handle.read_interrupt(aux_endpoint, &mut data, Duration::from_millis(100)) {
                    Ok(size) => {
                        if let Ok(mut state) = aux_state.lock() {
                            state.0 = state.0.wrapping_add(1);
                            state.1 = size;
                            state.2 = data;
                            state.3 = Instant::now();
                        }
                    }
                    Err(rusb::Error::Timeout) => {}
                    Err(_) => break,
                }
            }
        });
    }
    eprintln!("T1161 Control ativo (paisagem proporcional)");
    while !stop.load(Ordering::Relaxed) && deadline.is_none_or(|limit| Instant::now() < limit) {
        match usb_rx.recv_timeout(Duration::from_millis(10)) {
            Ok(Ok((size, incoming))) if size >= 13 && incoming[0] == 0x06 => {
                let data = incoming;
                let x = u16::from_be_bytes([data[1], data[2]]) as i32;
                let y = u16::from_be_bytes([data[3], data[4]]) as i32;
                let raw = u16::from_be_bytes([data[5], data[6]]) as i32;
                let pen_buttons = data[9];
                let tablet_buttons = u16::from_be_bytes([data[12], data[11]]);
                // Preserva a sensibilidade original; picos isolados são
                // rejeitados pela confirmação temporal logo abaixo.
                let pressure = map_pressure(raw, pressure_threshold, pressure_maximum);
                let detected_by_hardware = raw != presence_threshold;
                let sample = (x, y, pressure, pen_buttons, tablet_buttons);
                let sample_changed = last_sample != Some(sample);
                if sample_changed {
                    last_sample = Some(sample);
                    last_change = Instant::now();
                }
                present = match presence_mode {
                    2 => detected_by_hardware,
                    _ => true,
                };
                if pressure > 0 {
                    press_frames = press_frames.saturating_add(1);
                    release_frames = 0;
                    if press_frames >= press_required { touching = true; }
                } else {
                    press_frames = 0;
                    if touching {
                        release_frames = release_frames.saturating_add(1);
                        if release_frames >= release_required { touching = false; }
                    }
                }
                if next_config_read <= Instant::now() {
                    timeout = idle_timeout();
                    (presence_mode, presence_threshold) = presence_settings();
                    (pressure_threshold, pressure_maximum, press_required, release_required) = pressure_settings();
                    mapping = load_mapping();
                    input_profile = load_input_profile();
                    control_actions = load_control_actions(input_profile);
                    next_config_read = Instant::now() + Duration::from_secs(1);
                }
                if presence_mode == 1
                    && timeout.is_some_and(|duration| last_change.elapsed() >= duration) {
                    present = false;
                }
                if !present { touching = false; }
                let pen04 = pen_buttons == 0x04;
                let pen06 = pen_buttons == 0x06;
                let baseline = *tablet_baseline.get_or_insert(tablet_buttons);
                let tablet_changed = tablet_buttons ^ baseline;
                if next_menu_read <= Instant::now() {
                    menu_visible = fs::read_to_string("/dev/shm/t1161-menu-visible")
                        .is_ok_and(|value| value.trim() == "1");
                    next_menu_read = Instant::now() + Duration::from_millis(20);
                }
                let effective_tablet = if menu_visible {
                    tablet_changed & !control_actions.tablet_menu
                } else { tablet_changed };
                let touch_mode = selected_touch_mode(control_actions, effective_tablet, pen04, pen06);
                let configured_gesture = if action_touch_mode(control_actions.pen04) != TouchMode::None {
                    Gesture::None
                } else { input_profile.gesture04 };
                let (pen_gesture_active, isolated_pen04_click) =
                    pen04_gesture.update(pen04, touching, configured_gesture);
                let tablet_scroll_active = touching
                    && effective_tablet & control_actions.tablet_scroll != 0;
                let pen_scroll_active = touching && ((pen04 && control_actions.pen04 == ControlAction::PenScroll)
                    || (pen06 && control_actions.pen06 == ControlAction::PenScroll));
                let gesture_active = pen_gesture_active || tablet_scroll_active || pen_scroll_active;
                let active_scroll_gesture = if tablet_scroll_active || pen_scroll_active {
                    Gesture::ScrollVertical
                } else { configured_gesture };
                let mut wheel = (0, 0);
                if gesture_active {
                    if let Some((old_x, old_y)) = scroll_anchor {
                        scroll_remainder.0 += (x - old_x) as f64 * input_profile.scroll_speed / 80.0;
                        scroll_remainder.1 += (y - old_y) as f64 * input_profile.scroll_speed / 80.0;
                        match active_scroll_gesture {
                            Gesture::ScrollVertical => {
                                wheel.0 = -(scroll_remainder.1.trunc() as i32);
                                scroll_remainder.1 -= (-wheel.0) as f64;
                            }
                            Gesture::ScrollHorizontal => {
                                wheel.1 = scroll_remainder.0.trunc() as i32;
                                scroll_remainder.0 -= wheel.1 as f64;
                            }
                            Gesture::None => {}
                        }
                    }
                    scroll_anchor = Some((x, y));
                } else {
                    scroll_anchor = None;
                    scroll_remainder = (0.0, 0.0);
                }
                let synthesized_touch = touching && touch_mode != TouchMode::None;
                let effective_pressure = if gesture_active || synthesized_touch
                    || control_actions.tip == ControlAction::None { 0 } else { pressure };
                let effective_touch = touching && !gesture_active && !synthesized_touch
                    && control_actions.tip == ControlAction::Left;
                // Se 0x04 também inicia um gesto, seu clique isolado só é
                // confirmado ao soltar. Assim o clique não captura o ponteiro
                // antes de uma combinação de pressão + movimento.
                let defer_pen04 = configured_gesture != Gesture::None;
                let buttons = if gesture_active || synthesized_touch { (false, false, false) }
                    else { control_buttons(control_actions, touching,
                        pen04 && !defer_pen04, pen06, effective_tablet) };
                let output_distance = if touching { 0 } else { (raw - 1550).clamp(1, 255) };
                let mapped = mapping.as_ref().map_or(Some((x, y)), |config| transform_point(config, x, y));
                if let Some((output_x, output_y)) = mapped {
                    // Publica antes dos eventos de clique/gesto. A extensao do
                    // compositor mantem o cursor global nesta posicao absoluta.
                    pointer_state.seek(SeekFrom::Start(0))?;
                    write!(pointer_state, "{output_x:04} {output_y:04} {}\n", present as u8)?;
                    let pointer_state_len = pointer_state.stream_position()?;
                    pointer_state.set_len(pointer_state_len)?;
                    inside_mapping = true;
                    last_mapped_position = Some((output_x, output_y));
                    if isolated_pen04_click {
                        let click = control_buttons(control_actions, false, true, false, 0);
                        emit(&mut pen, output_x, output_y, 0, false,
                            present, output_distance)?;
                        emit_buttons(&mut button_device, output_x, output_y, click)?;
                    }
                    if sample_changed || last_emit.elapsed() >= Duration::from_millis(20) {
                        emit(&mut pen, output_x, output_y, effective_pressure, effective_touch,
                            present, output_distance)?;
                        emit_buttons(&mut button_device, output_x, output_y, buttons)?;
                        emit_scroll(&mut scroll, wheel)?;
                        emit_touch_gesture(&mut touchpad, output_x, output_y, pressure,
                            if synthesized_touch { touch_mode } else { TouchMode::None },
                            &mut touch_gesture, control_actions.touch_spacing)?;
                        last_emit = Instant::now();
                    }
                } else if inside_mapping {
                    if let Some((output_x, output_y)) = last_mapped_position {
                        emit(&mut pen, output_x, output_y, 0, false, present, output_distance,
                            )?;
                        emit_buttons(&mut button_device, output_x, output_y,
                            (false, false, false))?;
                    }
                    inside_mapping = false;
                }
                telemetry.seek(SeekFrom::Start(0))?;
                let auxiliary = auxiliary_state.lock().ok();
                let (aux_sequence, aux_size, aux_data, aux_age_ms) = auxiliary.as_ref()
                    .map(|state| (state.0, state.1, state.2, state.3.elapsed().as_millis()))
                    .unwrap_or((0, 0, [0; 8], 0));
                write!(telemetry,
                    "{x} {y} {pressure} {} {pen_buttons} {tablet_buttons} {} {} {} {} {} {} {aux_sequence} {aux_age_ms} {aux_size} {} {} {} {} {} {} {} {}\n",
                    touching as u8, data[1], data[2], data[3], data[4], data[5], data[6],
                    aux_data[0], aux_data[1], aux_data[2], aux_data[3],
                    aux_data[4], aux_data[5], aux_data[6], aux_data[7])?;
                let telemetry_len = telemetry.stream_position()?;
                telemetry.set_len(telemetry_len)?;
            }
            Ok(Ok(_)) | Err(mpsc::RecvTimeoutError::Timeout) => {
                if next_config_read <= Instant::now() {
                    timeout = idle_timeout();
                    (presence_mode, presence_threshold) = presence_settings();
                    (pressure_threshold, pressure_maximum, press_required, release_required) = pressure_settings();
                    mapping = load_mapping();
                    input_profile = load_input_profile();
                    control_actions = load_control_actions(input_profile);
                    next_config_read = Instant::now() + Duration::from_secs(1);
                }
                if present && timeout.is_some_and(|duration| last_change.elapsed() >= duration) {
                    present = false;
                    touching = false;
                    if let Some((x, y, _pressure, _pen_buttons, _tablet_buttons)) = last_sample {
                        emit(&mut pen, x, y, 0, false, false, 0)?;
                        let (button_x, button_y) = last_mapped_position.unwrap_or((x, y));
                        emit_buttons(&mut button_device, button_x, button_y,
                            (false, false, false))?;
                    }
                }
            }
            Ok(Err(rusb::Error::NoDevice)) | Err(mpsc::RecvTimeoutError::Disconnected) => break,
            Ok(Err(error)) => return Err(error.into()),
        }
    }
    let _ = emit(&mut pen, 0, 0, 0, false, false, 0);
    let (button_x, button_y) = last_mapped_position.unwrap_or((0, 0));
    let _ = emit_buttons(&mut button_device, button_x, button_y, (false, false, false));
    let _ = emit_touch_gesture(&mut touchpad, 0, 0, 0, TouchMode::None,
        &mut touch_gesture, control_actions.touch_spacing);
    eprintln!("T1161 Control encerrado");
    Ok(())
}
