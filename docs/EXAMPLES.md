# Automation Examples

This guide provides practical Home Assistant automation examples for Tuya BLE Mesh devices.

## Table of Contents

- [Basic Light Control](#basic-light-control)
- [Time-Based Automations](#time-based-automations)
- [Group Control](#group-control)
- [Device Identification](#device-identification)
- [Scenes and Scripts](#scenes-and-scripts)
- [Advanced Examples](#advanced-examples)

## Basic Light Control

### Turn On at Sunset

```yaml
automation:
  - alias: "Turn on mesh lights at sunset"
    trigger:
      - platform: sun
        event: sunset
        offset: "-00:30:00"  # 30 minutes before sunset
    action:
      - service: light.turn_on
        target:
          entity_id:
            - light.living_room_led
            - light.bedroom_led
        data:
          brightness_pct: 80
```

### Turn Off at Sunrise

```yaml
automation:
  - alias: "Turn off mesh lights at sunrise"
    trigger:
      - platform: sun
        event: sunrise
    action:
      - service: light.turn_off
        target:
          entity_id:
            - light.living_room_led
            - light.bedroom_led
```

### Fade In at Wake-Up Time

```yaml
automation:
  - alias: "Wake-up light fade in"
    trigger:
      - platform: time
        at: "07:00:00"
    action:
      - service: light.turn_on
        target:
          entity_id: light.living_room_led
        data:
          brightness_pct: 1
      - repeat:
          count: 20
          sequence:
            - delay: "00:01:00"  # 1 minute
            - service: light.turn_on
              target:
                entity_id: light.living_room_led
              data:
                brightness_pct: "{{ (repeat.index * 5) | int }}"
```

## Time-Based Automations

### Dim Lights Based on Time of Day

```yaml
automation:
  - alias: "Adaptive lighting - dim at night"
    trigger:
      - platform: time
        at: "22:00:00"
    action:
      - service: light.turn_on
        target:
          area_id: living_room
        data:
          brightness_pct: 30
          kelvin: 2700  # Warm white

  - alias: "Adaptive lighting - bright during day"
    trigger:
      - platform: time
        at: "08:00:00"
    action:
      - service: light.turn_on
        target:
          area_id: living_room
        data:
          brightness_pct: 100
          kelvin: 4000  # Cool white
```

### Evening Wind-Down Sequence

```yaml
automation:
  - alias: "Evening wind-down"
    trigger:
      - platform: time
        at: "20:00:00"
    action:
      # Dim lights gradually over 30 minutes
      - service: light.turn_on
        target:
          entity_id: light.living_room_led
        data:
          brightness_pct: 60
          transition: 1800  # 30 minutes in seconds

      # Switch to warm color temperature
      - service: light.turn_on
        target:
          entity_id: light.living_room_led
        data:
          kelvin: 2500
          transition: 1800
```

## Group Control

### Create a Light Group

First, create a light group in `configuration.yaml`:

```yaml
light:
  - platform: group
    name: "Mesh Lights"
    entities:
      - light.living_room_led
      - light.bedroom_ceiling
      - light.kitchen_downlights
```

### Control All Mesh Lights Together

```yaml
automation:
  - alias: "All mesh lights on at sunset"
    trigger:
      - platform: sun
        event: sunset
    action:
      - service: light.turn_on
        target:
          entity_id: light.mesh_lights
        data:
          brightness_pct: 80
          kelvin: 3000
```

### Sequential Light Activation

```yaml
automation:
  - alias: "Sequential light turn-on"
    trigger:
      - platform: state
        entity_id: binary_sensor.front_door
        to: "on"
    action:
      - service: light.turn_on
        target:
          entity_id: light.hallway_light
      - delay: "00:00:02"
      - service: light.turn_on
        target:
          entity_id: light.living_room_light
      - delay: "00:00:02"
      - service: light.turn_on
        target:
          entity_id: light.kitchen_light
```

### Zone-Based Control

```yaml
automation:
  - alias: "Turn on bedroom mesh devices"
    trigger:
      - platform: state
        entity_id: person.charlie
        to: "home"
    condition:
      - condition: time
        after: "18:00:00"
        before: "23:00:00"
    action:
      - service: light.turn_on
        target:
          area_id: bedroom
        data:
          brightness_pct: 50
```

## Device Identification

### Identify Device via Service Call

Useful for finding which physical device corresponds to an entity:

```yaml
automation:
  - alias: "Identify device - blink 3 times"
    trigger:
      - platform: state
        entity_id: input_button.identify_device
    action:
      - repeat:
          count: 3
          sequence:
            - service: light.turn_off
              target:
                entity_id: light.living_room_led
            - delay: "00:00:01"
            - service: light.turn_on
              target:
                entity_id: light.living_room_led
              data:
                brightness_pct: 100
            - delay: "00:00:01"
```

### Rainbow Effect for Identification

For RGB-capable devices:

```yaml
script:
  identify_rgb_light:
    alias: "Identify RGB light with rainbow"
    sequence:
      - repeat:
          count: 7
          sequence:
            - service: light.turn_on
              target:
                entity_id: light.malmbergs_rgb_bulb
              data:
                rgb_color:
                  - "{{ [255, 0, 0, 255, 165, 255, 255, 0, 255, 0, 255, 0, 128, 0, 255, 75, 0, 255, 238, 130, 238][repeat.index * 3 - 3] }}"
                  - "{{ [255, 0, 0, 255, 165, 255, 255, 0, 255, 0, 255, 0, 128, 0, 255, 75, 0, 255, 238, 130, 238][repeat.index * 3 - 2] }}"
                  - "{{ [255, 0, 0, 255, 165, 255, 255, 0, 255, 0, 255, 0, 128, 0, 255, 75, 0, 255, 238, 130, 238][repeat.index * 3 - 1] }}"
                brightness_pct: 100
            - delay: "00:00:01"
```

## Scenes and Scripts

### Good Night Scene

```yaml
scene:
  - name: "Good Night"
    entities:
      light.living_room_led:
        state: off
      light.bedroom_ceiling:
        state: off
      light.hallway_light:
        state: on
        brightness_pct: 10
        kelvin: 2500
      switch.malmbergs_smart_plug:
        state: off
```

Activate via automation:

```yaml
automation:
  - alias: "Good night - turn off all mesh devices"
    trigger:
      - platform: state
        entity_id: input_boolean.bedtime
        to: "on"
    action:
      - service: scene.turn_on
        target:
          entity_id: scene.good_night
```

### Morning Routine Script

```yaml
script:
  morning_routine:
    alias: "Morning routine"
    sequence:
      # Turn on bedroom light at low brightness
      - service: light.turn_on
        target:
          entity_id: light.bedroom_ceiling
        data:
          brightness_pct: 20
          kelvin: 2700

      # Wait 5 minutes
      - delay: "00:05:00"

      # Gradually increase brightness
      - service: light.turn_on
        target:
          entity_id: light.bedroom_ceiling
        data:
          brightness_pct: 80
          kelvin: 4000
          transition: 300  # 5 minutes

      # Turn on other lights
      - delay: "00:05:00"
      - service: light.turn_on
        target:
          area_id: bathroom
        data:
          brightness_pct: 100
```

### Movie Mode Script

```yaml
script:
  movie_mode:
    alias: "Movie mode"
    sequence:
      - service: light.turn_on
        target:
          entity_id: light.living_room_ceiling
        data:
          brightness_pct: 5
          kelvin: 2500
          transition: 2

      - service: light.turn_off
        target:
          entity_id:
            - light.kitchen_downlights
            - light.hallway_light
```

## Advanced Examples

### Motion-Activated Lighting with Timeout

```yaml
automation:
  - alias: "Motion lights on"
    trigger:
      - platform: state
        entity_id: binary_sensor.hallway_motion
        to: "on"
    action:
      - service: light.turn_on
        target:
          entity_id: light.hallway_light
        data:
          brightness_pct: 80

  - alias: "Motion lights off after timeout"
    trigger:
      - platform: state
        entity_id: binary_sensor.hallway_motion
        to: "off"
        for: "00:05:00"  # 5 minutes
    action:
      - service: light.turn_off
        target:
          entity_id: light.hallway_light
```

### Presence-Based Lighting

```yaml
automation:
  - alias: "Welcome home lighting"
    trigger:
      - platform: state
        entity_id: person.charlie
        to: "home"
    condition:
      - condition: sun
        after: sunset
    action:
      - service: light.turn_on
        target:
          entity_id: light.mesh_lights
        data:
          brightness_pct: 80
          kelvin: 3500

  - alias: "Away mode - turn off all mesh lights"
    trigger:
      - platform: state
        entity_id: person.charlie
        to: "not_home"
        for: "00:10:00"
    action:
      - service: light.turn_off
        target:
          entity_id: light.mesh_lights
```

### Low Signal Strength Alert

```yaml
automation:
  - alias: "Alert on weak mesh signal"
    trigger:
      - platform: numeric_state
        entity_id: sensor.malmbergs_led_driver_signal
        below: -80
        for: "00:05:00"
    action:
      - service: notify.mobile_app
        data:
          title: "Weak BLE Signal"
          message: >
            {{ state_attr(trigger.entity_id, 'friendly_name') }}
            has weak signal: {{ states(trigger.entity_id) }} dBm
      - service: persistent_notification.create
        data:
          title: "BLE Mesh Signal Issue"
          message: >
            Device {{ state_attr(trigger.entity_id, 'friendly_name') }}
            may need repositioning or a closer bridge/proxy.
```

### Adaptive Brightness Based on Ambient Light

```yaml
automation:
  - alias: "Adaptive brightness based on lux"
    trigger:
      - platform: numeric_state
        entity_id: sensor.living_room_illuminance
        below: 100
    action:
      - service: light.turn_on
        target:
          entity_id: light.living_room_ceiling
        data:
          brightness_pct: >
            {% set lux = states('sensor.living_room_illuminance') | float(0) %}
            {% if lux < 10 %}
              100
            {% elif lux < 50 %}
              80
            {% elif lux < 100 %}
              50
            {% else %}
              20
            {% endif %}
```

### Turn Off All Mesh Devices on Goodbye

```yaml
automation:
  - alias: "Goodbye - all mesh devices off"
    trigger:
      - platform: event
        event_type: goodbye_scene_activated
    action:
      - service: light.turn_off
        target:
          integration: tuya_ble_mesh
      - service: switch.turn_off
        target:
          integration: tuya_ble_mesh
```

### Firmware Update Notification

```yaml
automation:
  - alias: "Notify on firmware update available"
    trigger:
      - platform: state
        entity_id: update.malmbergs_led_driver_firmware
        to: "on"
    action:
      - service: notify.mobile_app
        data:
          title: "Firmware Update Available"
          message: >
            {{ state_attr(trigger.entity_id, 'friendly_name') }}
            has a firmware update available:
            {{ state_attr(trigger.entity_id, 'latest_version') }}
```

## Tips and Best Practices

### 1. Use Transitions for Smooth Changes

Always specify `transition` for gradual brightness/color changes:

```yaml
service: light.turn_on
data:
  brightness_pct: 50
  transition: 5  # seconds
```

### 2. Group Related Devices

Create light groups for easier bulk control:

```yaml
light:
  - platform: group
    name: "Downstairs Mesh Lights"
    entities:
      - light.living_room_ceiling
      - light.hallway_light
      - light.kitchen_downlights
```

### 3. Use Areas for Zone Control

Assign devices to areas in HA, then target entire areas:

```yaml
action:
  - service: light.turn_off
    target:
      area_id: bedroom
```

### 4. Leverage Conditions

Prevent unwanted triggers with conditions:

```yaml
condition:
  - condition: sun
    after: sunset
  - condition: state
    entity_id: person.charlie
    state: "home"
```

### 5. Monitor Signal Strength

Use RSSI sensors to identify connectivity issues:

```yaml
sensor:
  - platform: template
    sensors:
      mesh_network_health:
        friendly_name: "Mesh Network Health"
        value_template: >
          {% set signals = [
            states('sensor.light_1_signal'),
            states('sensor.light_2_signal'),
            states('sensor.light_3_signal')
          ] %}
          {% set avg = (signals | map('float', -100) | sum / signals | length) | round(1) %}
          {{ avg }}
        unit_of_measurement: "dBm"
```

## Related Documentation

- [Services Reference](SERVICES.md) — All available service calls
- [User Guide](USER_GUIDE.md) — Setup and configuration
- [Troubleshooting](USER_GUIDE.md#troubleshooting) — Common issues and solutions
- [Supported Devices](SUPPORTED_DEVICES.md) — Compatible hardware
