# WhatsApp Bridge

A bridge system that connects your WhatsApp client to external applications via a REST API. This allows you to send WhatsApp messages programmatically to specific JIDs identified during group analysis.

## Overview

The WhatsApp Bridge provides:
- A Go-based WhatsApp client that connects to WhatsApp Web using the `whatsmeow` library
- A REST API for sending messages to specific JIDs
- Integration with group analysis for automated follow-up messages
- Support for both text messages and media files

## Architecture

- `main.go` - Main entry point that initializes the bridge
- `bridge.go` - Core messaging functionality
- `api.go` - REST API server implementation
- `utils.go` - Utility functions for media processing
- `client.py` - Python client for interacting with the API
- `integration.py` - Integration with group analysis logic

## API Endpoints

### POST /api/send
Send a message to a WhatsApp contact or group.

**Request Body:**
```json
{
  "recipient": "1234567890@s.whatsapp.net",
  "message": "Hello from WhatsApp Bridge!",
  "media_path": "/path/to/media/file.jpg" (optional)
}
```

**Response:**
```json
{
  "success": true,
  "message": "Message sent to 1234567890@s.whatsapp.net"
}
```

### GET /api/health
Check the health status of the bridge.

**Response:**
```json
{
  "status": "healthy",
  "service": "whatsapp-bridge"
}
```

## Setup and Installation

### Prerequisites
- Go 1.24 or later
- SQLite3
- A WhatsApp account for the bot

### Installation

1. Clone the repository and navigate to the `whatsapp-bridge` directory:
```bash
cd whatsapp-mcp/whatsapp-bridge
```

2. Install dependencies:
```bash
go mod tidy
```

3. Run the bridge:
```bash
go run *.go
```

On first run, you'll need to scan a QR code with your WhatsApp app to authenticate the bridge.

## Usage with Group Analysis

The bridge can be integrated with the group analysis system to send automated follow-ups:

```python
from integration import GroupAnalyzerBridge
from client import WhatsAppBridgeClient

# Initialize the bridge client
bridge_client = WhatsAppBridgeClient()

# Initialize the analyzer bridge
analyzer_bridge = GroupAnalyzerBridge(
    bridge_client=bridge_client,
    whatsapp_db_path="store/whatsapp.db",
    messages_db_path="store/messages.db"
)

# Send follow-ups based on analysis
analysis_results = [
    {
        'jid': '1234567890@s.whatsapp.net',
        'name': 'John Doe',
        'task': 'Submit project report'
    }
]

followup_template = "Hi {name}! Following up on {task}. Could you please provide an update?"

send_results = analyzer_bridge.send_analysis_followups(analysis_results, followup_template)
```

## Environment Variables

- `PORT` - Port for the API server (default: 8080)

## Security Considerations

- The API does not have authentication by default
- In production, consider adding authentication and rate limiting
- Be careful with the WhatsApp session data stored in `store/whatsapp.db`

## Troubleshooting

- If you get connection errors, ensure your WhatsApp account is not restricted
- Check that the `store` directory has write permissions
- Verify your internet connection is stable

## License

This project is licensed under the terms specified in the main repository.