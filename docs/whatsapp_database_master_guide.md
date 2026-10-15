# WhatsApp Bridge Database Master Guide

This document provides a comprehensive overview of the database schema used in the WhatsApp bridge project. It details the structure, relationships, and purpose of all tables in both databases, and provides a guide for resolving contact information.

## 1. Database Overview

The WhatsApp bridge uses two SQLite databases:

1.  **`whatsapp.db`** - Contains device configuration, cryptographic data, contacts, and session information
2.  **`messages.db`** - Contains chat metadata and message content

This separation allows for better organization and performance, with configuration data separate from the potentially large message history.

## 2. Database Relationships Diagram

```
┌─────────────────────────┐                 ┌─────────────────────────┐
│      whatsapp.db        │                 │       messages.db       │
├─────────────────────────┤                 ├─────────────────────────┤
│ whatsmeow_device        │                 │ chats                   │
│  - jid (PK)             │◄────────┐       │  - jid (PK)             │
│  - registration_id      │         │       │  - name                 │
│  - noise_key            │         │       │  - last_message_time    │
│  - identity_key         │         │       │                         │
│  - ...                  │         │       │                         │
└─────────────────────────┘         │       └───────────┬─────────────┘
          ▲                         │                   │
          │                         │                   │
┌─────────┴─────────────┐          │       ┌───────────▼─────────────┐
│ whatsmeow_contacts    │          │       │ messages                │
│  - our_jid (PK)(FK)   │          │       │  - id (PK)              │
│  - their_jid (PK)     │          │       │  - chat_jid (PK)(FK)    │
│  - first_name         │          │       │  - sender               │
│  - full_name          │          │       │  - content              │
│  - push_name          │          │       │  - timestamp            │
│  - business_name      │          │       │  - is_from_me           │
└─────────┬─────────────┘          │       │  - media_type           │
          │                        │       │  - ...                  │
          │                        │       └───────────┬─────────────┘
┌─────────▼─────────────┐          │                   │
│ whatsmeow_message_    │          │                   │
│ secrets               │          │                   │
│  - our_jid (PK)(FK)   │◄─────────┘                   │
│  - chat_jid (PK)      │◄───────────────────────────┐ │
│  - sender_jid (PK)    │◄─────────┐                 │ │
│  - message_id (PK)    │◄─────────┼─────────────────┼─┘
│  - key                │          │                 │
└─────────────────────┬─┘          │                 │
                      │            │                 │
                      │            │                 │
                      ▼            ▼                 ▼
             Resolves actual   Maps to actual    Links to message
             sender in groups  contact info      in messages.db
```

## 3. Database Schema Details

### 3.1. `whatsapp.db`

#### `whatsmeow_device`
The central table that stores device identity and cryptographic material.

| Field Name           | Type    | Description                                                  |
|----------------------|---------|--------------------------------------------------------------|
| jid                  | TEXT    | **Primary Key.** WhatsApp JID (Jabber ID) for the device.    |
| registration_id      | BIGINT  | Registration ID for the device (unsigned 32-bit integer).    |
| noise_key            | bytea   | 32-byte Noise protocol key for secure communication.         |
| identity_key         | bytea   | 32-byte identity key for the device.                         |
| signed_pre_key       | bytea   | 32-byte signed pre-key for session establishment.            |
| signed_pre_key_id    | INTEGER | ID of the signed pre-key (unsigned 24-bit integer).          |
| signed_pre_key_sig   | bytea   | 64-byte signature for the signed pre-key.                    |
| adv_key              | bytea   | Advertisement key for device identity.                       |
| adv_details          | bytea   | Advertisement details for device identity.                   |
| adv_account_sig      | bytea   | 64-byte account signature for device advertisement.          |
| adv_device_sig       | bytea   | 64-byte device signature for device advertisement.           |
| platform             | TEXT    | Device platform (e.g., Android, iOS, Web).                   |
| business_name        | TEXT    | Business name associated with the device (if any).           |
| push_name            | TEXT    | Push name (display name) for the device.                     |
| adv_account_sig_key  | bytea   | 32-byte key for account signature (optional).                |
| facebook_uuid        | uuid    | Facebook UUID associated with the device (optional).         |

**Purpose**: Stores the device identity and cryptographic material needed for WhatsApp's end-to-end encryption. This is the foundation of the WhatsApp connection.

#### `whatsmeow_contacts`
Stores contact information for WhatsApp users.

| Field Name    | Type  | Description                                        |
|---------------|-------|----------------------------------------------------|
| our_jid       | TEXT  | **Primary Key (1).** JID of our device (FK).       |
| their_jid     | TEXT  | **Primary Key (2).** JID of the contact.           |
| first_name    | TEXT  | First name of the contact.                         |
| full_name     | TEXT  | Full name of the contact.                          |
| push_name     | TEXT  | Push name (display name) of the contact.           |
| business_name | TEXT  | Business name of the contact (if any).             |

**Purpose**: Maintains a directory of all contacts, including their display names and identifiers. This table is crucial for resolving JIDs to human-readable names.

#### `whatsmeow_message_secrets`
Maps message IDs to actual sender JIDs, especially important for group chats.

| Field Name  | Type  | Description                                          |
|-------------|-------|------------------------------------------------------|
| our_jid     | TEXT  | **Primary Key (1).** JID of our device (FK).         |
| chat_jid    | TEXT  | **Primary Key (2).** JID of the chat.                |
| sender_jid  | TEXT  | **Primary Key (3).** JID of the sender.              |
| message_id  | TEXT  | **Primary Key (4).** ID of the message.              |
| key         | bytea | Secret key for the message.                          |

**Purpose**: Critical for resolving message senders in group chats. The `messages` table in `messages.db` stores the group JID as sender, while this table maps the message ID to the actual individual sender's JID.

#### `whatsmeow_chat_settings`
Stores user-specific chat settings including archive status.

| Field Name   | Type    | Description                                        |
|--------------|--------|----------------------------------------------------|
| our_jid      | TEXT    | **Primary Key (1).** JID of our device (FK).       |
| chat_jid     | TEXT    | **Primary Key (2).** JID of the chat.              |
| muted_until  | BIGINT  | Timestamp until which the chat is muted.           |
| pinned       | BOOLEAN | True if the chat is pinned.                        |
| archived     | BOOLEAN | **True if the chat is archived, false otherwise.** |

**Purpose**: Stores user preferences for individual chats, including archive status which is critical for determining if a chat has been archived by the user.

**Archive Detection**: The `archived` field is the definitive way to detect if a WhatsApp chat has been archived. A value of `1` (true) indicates the chat is archived, while `0` (false) indicates it is not archived.

#### Other `whatsapp.db` tables
This database also contains other tables for managing cryptographic material and session state, such as `whatsmeow_identity_keys`, `whatsmeow_pre_keys`, `whatsmeow_sessions`, `whatsmeow_sender_keys`, `whatsmeow_app_state_sync_keys`, `whatsmeow_app_state_version`, `whatsmeow_app_state_mutation_macs`, `whatsmeow_privacy_tokens`, and `whatsmeow_version`. These are primarily for the internal workings of the `whatsmeow` library.

### 3.2. `messages.db`

#### `chats`
Stores metadata about conversations.

| Field Name        | Type      | Description                                  |
|-------------------|-----------|----------------------------------------------|
| jid               | TEXT      | **Primary Key.** WhatsApp JID for the chat.  |
| name              | TEXT      | Display name of the chat.                    |
| last_message_time | TIMESTAMP | Timestamp of the last message in the chat.   |

**Purpose**: Maintains a list of all conversations (both individual and group) with their display names and last activity time.

#### `messages`
Stores the actual message content and metadata.

| Field Name      | Type      | Description                                    |
|-----------------|-----------|------------------------------------------------|
| id              | TEXT      | **Primary Key (1).** Message ID.               |
| chat_jid        | TEXT      | **Primary Key (2).** JID of the chat (FK).     |
| sender          | TEXT      | Sender identifier (group JID for group chats). |
| content         | TEXT      | Text content of the message.                   |
| timestamp       | TIMESTAMP | When the message was sent.                     |
| is_from_me      | BOOLEAN   | True if sent by the user, false if received.   |
| media_type      | TEXT      | Type of media (image, video, audio, document). |
| filename        | TEXT      | Filename for media messages.                   |
| url             | TEXT      | URL for media content.                         |
| media_key       | BLOB      | Key for decrypting media.                      |
| file_sha256     | BLOB      | SHA256 hash of the file.                       |
| file_enc_sha256 | BLOB      | SHA256 hash of the encrypted file.             |
| file_length     | INTEGER   | Size of the file in bytes.                     |

**Purpose**: Stores all message content, including text and metadata for media messages. For group messages, the `sender` field contains the group JID, not the individual sender's JID.

## 4. Contact & Sender Resolution Guide

This section provides a comprehensive guide on how to trace and resolve contact names from the various identifiers found in the WhatsApp databases.

### 4.1. Overview of Identifiers

There are three main types of identifiers you will encounter for a user:

*   **JID (Jabber ID):** The primary identifier for a WhatsApp user, in the format `[phone_number]@s.whatsapp.net`. For example: `91XXXXXXXXXX@s.whatsapp.net`.
*   **LID (Local ID):** A numerical identifier that WhatsApp uses internally. These are often seen in the AI model's output when it extracts a "person" from a message. For example: `219541632213229`.
*   **Phone Number:** The user's phone number, usually with the country code. For example: `91XXXXXXXXXX`.

### 4.2. Contact Resolution Flow

The goal is to get a JID, which can then be used to query the `whatsmeow_contacts` table for a name. Here is the resolution flow depending on the identifier you have:

#### Case 1: You have a LID

1.  **LID to Phone Number:** Query the `whatsmeow_lid_map` table in `whatsapp.db` to find the phone number (`pn`) associated with the `lid`.
2.  **Phone Number to JID:** Construct the JID by appending `@s.whatsapp.net` to the phone number.
3.  **JID to Name:** Query the `whatsmeow_contacts` table with the JID to get the name.

**SQL Example:**
```sql
-- Step 1: Get phone number from LID
SELECT pn FROM whatsmeow_lid_map WHERE lid = '219541632213229';
-- This will return '91XXXXXXXXXX'

-- Step 2: Use the phone number to get the name
SELECT push_name, full_name, first_name FROM whatsmeow_contacts WHERE their_jid = '91XXXXXXXXXX@s.whatsapp.net';
```

#### Case 2: You have a Phone Number

1.  **Phone Number to JID:** Construct the JID by appending `@s.whatsapp.net` to the phone number.
2.  **JID to Name:** Query the `whatsmeow_contacts` table with the JID to get the name.

#### Case 3: You have a JID

1.  **JID to Name:** Query the `whatsmeow_contacts` table with the JID to get the name.

### 4.3. Name Resolution Priority

When querying the `whatsmeow_contacts` table, you should prioritize the name fields in the following order, as some fields might be empty:

1.  `push_name`: The user's display name on WhatsApp. This is often the most reliable.
2.  `full_name`: The full name from your contacts.
3.  `first_name`: The first name from your contacts.
4.  `business_name`: If it's a business account.

**SQL `COALESCE` Example:**
```sql
SELECT COALESCE(push_name, full_name, first_name, business_name) as display_name
FROM whatsmeow_contacts
WHERE their_jid = '91XXXXXXXXXX@s.whatsapp.net';
```

### 4.4. Resolving the Sender of a Group Message

This is the most critical cross-database relationship. In group chats, the `sender` field in the `messages` table contains the group JID, not the individual sender's JID.

**Flow:**
1.  Take the `id` and `chat_jid` from a row in `messages.db`.`messages`.
2.  Use these to find the matching row in `whatsapp.db`.`whatsmeow_message_secrets`.
3.  The `sender_jid` in that `whatsmeow_message_secrets` row is the actual sender's JID.
4.  Use this `sender_jid` to look up the contact's name in `whatsapp.db`.`whatsmeow_contacts`.

**SQL Query Example:**
```sql
SELECT
    m.id,
    m.content,
    m.timestamp,
    COALESCE(
        c.full_name,
        c.first_name,
        c.push_name,
        c.business_name,
        SUBSTR(ms.sender_jid, 1, INSTR(ms.sender_jid, '@') - 1)
    ) as sender_name,
    m.is_from_me
FROM messages m
JOIN whatsapp_db.whatsmeow_message_secrets ms ON (
    m.id = ms.message_id AND
    m.chat_jid = ms.chat_jid
)
LEFT JOIN whatsapp_db.whatsmeow_contacts c ON ms.sender_jid = c.their_jid
WHERE m.chat_jid = '[GROUP_JID_HERE]'
ORDER BY m.timestamp ASC;
```
*Note: This query requires attaching `whatsapp.db` to the connection for `messages.db`.*

## 5. JID Format Patterns

Understanding JID formats is crucial for working with these databases:

- **Individual chats**: `[phone_number]@s.whatsapp.net` (e.g., `919876543210@s.whatsapp.net`)
- **Group chats**: `[group_id]@g.us` (e.g., `120363049581425328@g.us`)
- **Status broadcasts**: `status@broadcast`
- **Device JIDs**: `[phone_number]:[device_id]@s.whatsapp.net` (e.g., `919876543210:4@s.whatsapp.net`)

## 6. Practical Queries

### Get All Archived Chats
```sql
SELECT
    cs.chat_jid,
    c.name
FROM whatsmeow_chat_settings cs
LEFT JOIN messages_db.chats c ON cs.chat_jid = c.jid
WHERE cs.archived = 1;
```

### Count Messages by Sender in a Group
```sql
SELECT
    COALESCE(c.full_name, c.push_name, c.first_name,
             SUBSTR(ms.sender_jid, 1, INSTR(ms.sender_jid, '@') - 1)) as sender_name,
    COUNT(*) as message_count
FROM messages_db.messages m
JOIN whatsmeow_message_secrets ms ON m.id = ms.message_id AND m.chat_jid = ms.chat_jid
LEFT JOIN whatsmeow_contacts c ON ms.sender_jid = c.their_jid
WHERE m.chat_jid = '[GROUP_JID_HERE]'
GROUP BY ms.sender_jid
ORDER BY message_count DESC;
```