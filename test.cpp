ROBOT ELECTRICAL WIRING DIAGRAM (LEFT TO RIGHT, FULL VERSION)

[ 3S LiPo Battery ]
(11.1V nominal, 12.6V full)
          |
          v
[ Inline Fuse ]
          |
          v
[ Main Power Switch ]
          |
          v
[ VBAT Distribution Terminal / Raw Battery Rail ]
          |------------------------------> [ Motor Driver / Other High-Power Loads ]
          |
          v
[ DC-DC Buck Converter ]
(VBAT -> regulated 5V)
          |
          v
[ 5V Distribution Terminal / 5V Bus ]
        /          |             \
       v           v              v
[ Raspberry Pi ] [ Powered USB Hub ] [ Arduino / Sensors / Other 5V Devices ]

[ Powered USB Hub ] ---- USB Data Cable ----> [ Raspberry Pi ]

COMMON GROUND:
Battery GND -> VBAT GND -> Buck GND -> 5V Bus GND -> Pi GND / Hub GND / Motor Driver GND / Sensor GND