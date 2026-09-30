# Copyright 2026 Ib Helmer Nielsen
# SPDX-License-Identifier: Apache-2.0
"""Optional boot entry point. Manual startup is the safe deployment default."""
import board_config

if board_config.AUTOSTART:
    import time
    print("PyNetStack starts in %d ms; Ctrl-C cancels startup." % board_config.STARTUP_DELAY_MS)
    time.sleep_ms(board_config.STARTUP_DELAY_MS)
    import esp32c6_node
    esp32c6_node.run()
else:
    print("PyNetStack installed. Run: import esp32c6_node; esp32c6_node.run()")
