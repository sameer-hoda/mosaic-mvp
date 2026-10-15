import json
import sqlite3
from typing import List, Dict, Optional
import sys
import os

# Add the current directory to sys.path to allow importing from the same directory
current_dir = os.path.dirname(os.path.abspath(__file__))
if current_dir not in sys.path:
    sys.path.append(current_dir)

from client import WhatsAppBridgeClient
from datetime import datetime, timedelta
import pytz


class GroupAnalyzerBridge:
    """
    Bridge between the GroupAnalyzer and the WhatsApp Bridge API to send messages
    to members identified during analysis.
    """
    
    def __init__(self, bridge_client: WhatsAppBridgeClient, whatsapp_db_path: str, messages_db_path: str):
        """
        Initialize the bridge with the WhatsApp Bridge client and database paths.
        
        Args:
            bridge_client: WhatsAppBridgeClient instance
            whatsapp_db_path: Path to the whatsapp.db SQLite file
            messages_db_path: Path to the messages.db SQLite file
        """
        self.bridge_client = bridge_client
        self.whatsapp_db_path = whatsapp_db_path
        self.messages_db_path = messages_db_path
        
    def resolve_contact_name(self, identifier: str) -> str:
        """
        Resolves a contact identifier (JID, LID, or phone number) to a human-readable name.
        
        Args:
            identifier: Can be a JID, LID, or phone number string
            
        Returns:
            Human-readable name or the identifier if not found
        """
        if not (self.whatsapp_db_path and sqlite3.connect(self.whatsapp_db_path).execute("SELECT name FROM sqlite_master WHERE type='table'").fetchall()):
            print(f"❌ Error: whatsapp.db not found at {self.whatsapp_db_path}")
            return identifier

        try:
            conn = sqlite3.connect(f"file:{self.whatsapp_db_path}?mode=ro", uri=True)

            # Check if it's a LID (numeric identifier)
            if identifier.isdigit():
                # LID to phone number mapping
                lid_query = "SELECT pn FROM whatsmeow_lid_map WHERE lid = ?"
                cursor = conn.execute(lid_query, (identifier,))
                result = cursor.fetchone()
                if result:
                    phone_number = result[0]
                    jid = f"{phone_number}@s.whatsapp.net"
                else:
                    conn.close()
                    return identifier
            # Check if it's a phone number (starts with digits)
            elif identifier.replace('+', '').replace('-', '').isdigit():
                # Assume it's a phone number and convert to JID
                jid = f"{identifier}@s.whatsapp.net"
            # Assume it's already a JID
            else:
                jid = identifier

            # Look up the name in contacts table
            contact_query = """
            SELECT COALESCE(push_name, full_name, first_name, business_name)
            FROM whatsmeow_contacts
            WHERE their_jid = ?
            """
            cursor = conn.execute(contact_query, (jid,))
            result = cursor.fetchone()

            if result and result[0]:
                name = result[0]
                conn.close()
                return name
            else:
                # If not found in contacts, try to get from chats table
                chat_query = "SELECT name FROM chats WHERE jid = ?"
                cursor = conn.execute(chat_query, (jid,))
                result = cursor.fetchone()
                
                if result and result[0]:
                    name = result[0]
                    conn.close()
                    return name
                else:
                    conn.close()
                    return jid  # Return the JID if no name is found
        except Exception as e:
            print(f"Error resolving contact name: {e}")
            return identifier

    def get_group_members_with_activity(self, group_jid: str, hours_back: int = 24) -> List[Dict]:
        """
        Get group members with their activity from the specified time period.
        
        Args:
            group_jid: The JID of the group
            hours_back: Number of hours to look back for activity
            
        Returns:
            List of dictionaries containing member info and activity
        """
        if not (sqlite3.connect(self.messages_db_path).execute("SELECT name FROM sqlite_master WHERE type='table'").fetchall()):
            raise FileNotFoundError(f"messages.db not found at {self.messages_db_path}")

        try:
            # Connect to messages database and attach whatsapp database
            messages_conn = sqlite3.connect(self.messages_db_path)
            messages_conn.execute("ATTACH DATABASE ? AS whatsapp_db", (self.whatsapp_db_path,))

            # Calculate cutoff time
            ist = pytz.timezone('Asia/Kolkata')
            cutoff_time = datetime.now(ist) - timedelta(hours=hours_back)

            # Format datetime to match database format
            cutoff_time_str = cutoff_time.strftime('%Y-%m-%d %H:%M:%S+05:30')

            # Query for messages in the group within the time period
            query = """
            SELECT DISTINCT m.sender, c.push_name, c.full_name, c.first_name, 
                   MAX(m.timestamp) as last_message_time,
                   COUNT(*) as message_count
            FROM messages m
            LEFT JOIN whatsapp_db.whatsmeow_contacts c ON m.sender = c.their_jid
            WHERE m.chat_jid = ?
            AND m.timestamp >= ?
            GROUP BY m.sender, c.push_name, c.full_name, c.first_name
            ORDER BY last_message_time DESC
            """

            cursor = messages_conn.execute(query, (group_jid, cutoff_time_str))
            results = cursor.fetchall()
            messages_conn.close()

            members = []
            for sender, push_name, full_name, first_name, last_msg_time, msg_count in results:
                # Determine the best name to use
                name = next(filter(None, [full_name, push_name, first_name, sender.split('@')[0]]), sender)
                
                member_info = {
                    'jid': sender,
                    'name': name,
                    'last_message_time': last_msg_time,
                    'message_count': msg_count,
                    'resolved_name': self.resolve_contact_name(sender)
                }
                members.append(member_info)

            return members

        except Exception as e:
            print(f"Error getting group members: {e}")
            return []

    def send_followup_message(self, recipient_jid: str, message: str, media_path: Optional[str] = None) -> dict:
        """
        Send a follow-up message to a specific JID using the bridge.
        
        Args:
            recipient_jid: The JID of the recipient
            message: The message to send
            media_path: Optional path to a media file to send with the message
            
        Returns:
            A dictionary containing the result of the send operation
        """
        print(f"Sending follow-up message to {recipient_jid}: {message[:50]}...")
        result = self.bridge_client.send_message(recipient_jid, message, media_path)
        print(f"Send result: {result}")
        return result

    def send_bulk_messages(self, recipients: List[str], message: str, media_path: Optional[str] = None) -> List[dict]:
        """
        Send the same message to multiple recipients.
        
        Args:
            recipients: List of recipient JIDs
            message: The message to send
            media_path: Optional path to a media file to send with the message
            
        Returns:
            List of dictionaries containing the result of each send operation
        """
        results = []
        for recipient in recipients:
            result = self.send_followup_message(recipient, message, media_path)
            results.append(result)
        return results

    def send_analysis_followups(self, analysis_results: List[Dict], followup_message_template: str) -> List[dict]:
        """
        Send follow-up messages based on analysis results.
        
        Args:
            analysis_results: List of analysis results from GroupAnalyzer
            followup_message_template: Template string for the follow-up message
            
        Returns:
            List of dictionaries containing the result of each send operation
        """
        results = []
        
        for analysis in analysis_results:
            jid = analysis.get('jid', '')
            name = analysis.get('name', '')
            context = analysis.get('context', '')
            
            if jid:
                # Format the follow-up message using the template
                try:
                    message = followup_message_template.format(
                        name=name,
                        jid=jid,
                        context=context,
                        **analysis  # Allow access to other keys in the analysis
                    )
                except KeyError as e:
                    print(f"Error formatting message template: {e}")
                    message = followup_message_template  # Use template as-is if formatting fails
                
                result = self.send_followup_message(jid, message)
                results.append({
                    'jid': jid,
                    'name': name,
                    'result': result
                })
        
        return results


# Example usage function
def example_usage():
    """
    Example of how to use the GroupAnalyzerBridge with analysis results.
    """
    # Initialize the bridge client
    bridge_client = WhatsAppBridgeClient()
    
    # Initialize the group analyzer bridge
    analyzer_bridge = GroupAnalyzerBridge(
        bridge_client=bridge_client,
        whatsapp_db_path="store/whatsapp.db",
        messages_db_path="store/messages.db"
    )
    
    # Example analysis results (these would come from your GroupAnalyzer)
    analysis_results = [
        {
            'jid': '1234567890@s.whatsapp.net',
            'name': 'John Doe',
            'context': 'Was asked to provide project status update by Friday',
            'task': 'Submit project report'
        },
        {
            'jid': '0987654321@s.whatsapp.net',
            'name': 'Jane Smith',
            'context': 'Mentioned issues with deployment',
            'task': 'Fix deployment issues'
        }
    ]
    
    # Define a follow-up message template
    followup_template = """
Hi {name}! 👋

Following up on our discussion about "{task}". Could you please provide an update on this?

Context: {context}

Looking forward to hearing from you! 📅
    """.strip()
    
    # Send follow-up messages based on analysis
    send_results = analyzer_bridge.send_analysis_followups(analysis_results, followup_template)
    
    for result in send_results:
        print(f"Sent to {result['name']}: {result['result']['message']}")


if __name__ == "__main__":
    example_usage()