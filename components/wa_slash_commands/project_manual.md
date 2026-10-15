# Project Manual: Standalone Standout

## Context
This sandbox project (`wa-slash-commands`) represents the successful extraction of the deeply integrated WhatsApp Slash Commands from their legacy monolithic/EC2 deployment into a standalone, portable, open-source-ready library. 

The original integration lived directly within the server's user folder (`~/wa_productivity/`). For this standalone extraction, several structural guarantees had to be established.

## Key Changes in Standalone Version

### 1. `wacmd.py` vs Legacy `hotword_handler_v2.py`
The legacy script blindly assumed any incoming webhook should trigger a command. In this standalone iteration, **Owner Verification** was implemented at the very top of the routing layer (`wacmd.py`). It uses the `OWNER_PHONE_NUMBER` loaded from the `.env` to ensure that only the bot's owner is permitted to run expensive AI commands. This prevents abuse in large groups.

### 2. Environmental Decoupling (`engine.py`)
Previously, `slash_cmd_engine.py` hardcoded the SQLite database paths (`MESSAGES_DB_PATH = "~/whatsapp-mcp/whatsapp-bridge/store/messages.db"`). The new `engine.py` is fully environmentally decoupled. It looks for variables like `MESSAGES_DB_PATH` in the `.env` file and defaults to a local `./store/` directory relative to the repository if undefined. This allows a user cloning the repository to seamlessly direct the script to their own data sources.

### 3. Localized Caching
The original implementation wrote cache files into `~/wa_productivity/slash_cmd_cache`. The standalone version creates a localized `./cache` folder inside the project root, keeping the repository boundary contained.

## Future Potential
This sandbox is isolated and strictly meant to document and visualize the slash command component. Next steps for this project could include:
1. Shipping a built-in mock database so users can test the CLI without needing a real `whatsmeow` bridge.
2. Converting the `.env` setup instructions into an automated Python setup script.
3. Adding support for models outside the Gemini ecosystem.
