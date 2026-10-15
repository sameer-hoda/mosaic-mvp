import requests
import json
from typing import Optional
import os

class WhatsAppBridgeClient:
    """
    A client to interact with the WhatsApp Bridge API for sending messages to JIDs.
    """
    
    def __init__(self, base_url: str = "http://localhost:8080"):
        """
        Initialize the WhatsApp Bridge client.
        
        Args:
            base_url: The base URL of the WhatsApp Bridge API server
        """
        self.base_url = base_url.rstrip('/')
        
    def send_message(self, recipient: str, message: str, media_path: Optional[str] = None) -> dict:
        """
        Send a message to a WhatsApp JID.
        
        Args:
            recipient: The recipient's JID (e.g., "1234567890@s.whatsapp.net") or phone number
            message: The text message to send
            media_path: Optional path to a media file to send with the message
            
        Returns:
            A dictionary containing the API response
        """
        url = f"{self.base_url}/api/send"
        
        payload = {
            "recipient": recipient,
            "message": message
        }
        
        if media_path:
            payload["media_path"] = media_path
            
        headers = {}
        token = os.getenv("MOSAIC_BRIDGE_TOKEN", "")
        if token:
            headers["X-Bridge-Token"] = token

        try:
            response = requests.post(url, json=payload, headers=headers)
            response.raise_for_status()
            return response.json()
        except requests.exceptions.RequestException as e:
            return {
                "success": False,
                "message": f"Error sending message: {str(e)}"
            }
    
    def check_health(self) -> dict:
        """
        Check the health status of the WhatsApp Bridge API.
        
        Returns:
            A dictionary containing the health status
        """
        url = f"{self.base_url}/api/health"
        
        try:
            response = requests.get(url)
            response.raise_for_status()
            return response.json()
        except requests.exceptions.RequestException as e:
            return {
                "status": "unhealthy",
                "message": f"Health check failed: {str(e)}"
            }


# Example usage
if __name__ == "__main__":
    # Initialize the client
    client = WhatsAppBridgeClient()
    
    # Check health
    health = client.check_health()
    print(f"Health status: {health}")
    
    # Example send message (replace with actual JID)
    # result = client.send_message("1234567890@s.whatsapp.net", "Hello from WhatsApp Bridge!")
    # print(f"Send result: {result}")