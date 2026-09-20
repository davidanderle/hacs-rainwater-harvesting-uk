# RainWater Harvesting LTD Rain Director RS485 Protocol - Reverse Engineering
Fully reverse-engineered RS485 protocol for the [Rain Director](https://www.rainwaterharvesting.co.uk/product/rain-director/) control unit. No public protocol documentation or HACS integration existed prior to this work.

This project is an independent Home Assistant integration and is not affiliated with, endorsed by, or sponsored by RainWater Harvesting LTD. RainWater Harvesting LTD and the RainWater Harvesting LTD logo are trademarks of their respective owners.

# Home Assistant Integration (**Work in progress**)
This integration uses an Elfin-EW11A RS485 to WiFi controller to sniff the traffic between the modules inside the Rain Director, and the external attic tank level sensor.

# How to run
```
nc 192.168.1.46 8899 | python3 traffic_decoder.py
```
