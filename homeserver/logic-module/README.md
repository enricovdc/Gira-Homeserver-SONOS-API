# Gira HomeServer logic module

Native HSL3 logic modules that run inside the HomeServer's own logic
engine. Two LBS blocks (Sonos Player + Sonos Discover) plus the SOAP
envelope reference embedded in their Python source.

## Layout

```
hsl3/        HSL3 / Python 3.9 LogicModule sources, configs, help, build tooling.
             Run build/build_hslz.py to package .hslz archives for Experte import.
             See hsl3/README.md for the full reference.

soap/        Raw SOAP envelope templates (XML) and NOTIFY regex patterns,
             kept as a wire-format reference. Not used directly in Experte —
             the same envelopes are embedded as string constants in
             hsl3/src_22000_sonos_player/hsl3_22000_sonos_player.py.
             Useful when verifying behaviour against a different Sonos
             firmware or when porting the integration to another platform.
```

## Build, test, import

```sh
# 1. Build the .hslz archives (run on a machine with the Gira HSL3 generator)
python3 hsl3/build/build_hslz.py

# 2. Run the test suite (no HomeServer required)
python3 hsl3/build/test_logic_modules.py

# 3. In Experte: Logikbausteine → Importieren → select the two .hslz files
```

Full details: [hsl3/README.md](hsl3/README.md).
