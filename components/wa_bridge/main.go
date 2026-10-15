package main

import (
	"bytes"
	"context"
	"crypto/hmac"
	cryptorand "crypto/rand"
	"crypto/sha256"
	"database/sql"
	_ "embed"
	"encoding/base64"
	"encoding/binary"
	"encoding/hex"
	"encoding/json"
	"fmt"
	"io"
	"math"
	"math/rand"
	"net/http"
	"os"
	"os/signal"
	"path/filepath"
	"reflect"
	"strconv"
	"strings"
	"sync"
	"syscall"
	"time"

	_ "github.com/mattn/go-sqlite3"
	"github.com/mdp/qrterminal"
	qrcode "github.com/skip2/go-qrcode"
	"go.mau.fi/whatsmeow"
	waProto "go.mau.fi/whatsmeow/binary/proto"
	"go.mau.fi/whatsmeow/store/sqlstore"
	"go.mau.fi/whatsmeow/types"
	"go.mau.fi/whatsmeow/types/events"
	waLog "go.mau.fi/whatsmeow/util/log"
	"google.golang.org/protobuf/proto"
)

//go:embed wizard.html
var wizardHTML []byte

// ────────────────────────────────────────────────────────────────────
// STORE_DIR helper
// ────────────────────────────────────────────────────────────────────

func storePath(name string) string {
	dir := os.Getenv("STORE_DIR")
	if dir == "" {
		dir = "store"
	}
	return filepath.Join(dir, name)
}

// ────────────────────────────────────────────────────────────────────
// Setup state machine
// ────────────────────────────────────────────────────────────────────

type SetupState string

const (
	StateNeedsAPIKey SetupState = "NEEDS_API_KEY"
	StateNeedsQR     SetupState = "NEEDS_QR"
	StateNeedsMeChat SetupState = "NEEDS_MECHAT_PAIR"
	StateReady       SetupState = "READY"
	StateResetting   SetupState = "RESETTING"
)

type SetupData struct {
	State        string `json:"state"`
	GeminiKeySet bool   `json:"gemini_key_set"`
	OwnPhone     string `json:"own_phone"`
	OwnJID       string `json:"own_jid"`
	MeChatJID    string `json:"mechat_jid"`
	MeChatName   string `json:"mechat_name"`
	PairedAt     string `json:"paired_at"`
	UpdatedAt    string `json:"updated_at"`
}

var (
	setupMu   sync.RWMutex
	setupData SetupData
	startTime time.Time
)

func loadSetup() {
	setupMu.Lock()
	defer setupMu.Unlock()

	data, err := os.ReadFile(storePath("setup.json"))
	if err != nil {
		setupData = SetupData{
			State:     string(StateNeedsAPIKey),
			UpdatedAt: time.Now().Format(time.RFC3339),
		}
		saveSetupLocked()
		return
	}
	json.Unmarshal(data, &setupData)
	if setupData.State == "" {
		setupData.State = string(StateNeedsAPIKey)
	}
	// Re-resolve: stored state must match actual conditions
	resolved := resolveStateLocked()
	if resolved == StateNeedsAPIKey && setupData.State != string(StateNeedsAPIKey) {
		setupData.State = string(StateNeedsAPIKey)
	}
	if resolved == StateNeedsQR && setupData.State == string(StateNeedsAPIKey) && setupData.GeminiKeySet {
		setupData.State = string(StateNeedsQR)
	}
	setupData.UpdatedAt = time.Now().Format(time.RFC3339)
	saveSetupLocked()
}

func saveSetup() {
	setupMu.Lock()
	saveSetupLocked()
	setupMu.Unlock()
}

func saveSetupLocked() {
	data, err := json.Marshal(setupData)
	if err != nil {
		return
	}
	tmpPath := storePath("setup.json.tmp")
	if err := os.WriteFile(tmpPath, data, 0600); err != nil {
		return
	}
	os.Rename(tmpPath, storePath("setup.json"))
}

func resolveState() SetupState {
	setupMu.RLock()
	defer setupMu.RUnlock()
	return resolveStateLocked()
}

func resolveStateLocked() SetupState {
	if !setupData.GeminiKeySet {
		return StateNeedsAPIKey
	}
	if setupData.OwnPhone == "" {
		return StateNeedsQR
	}
	if setupData.MeChatJID == "" {
		return StateNeedsMeChat
	}
	return StateReady
}

func isSetupReady() bool {
	setupMu.RLock()
	defer setupMu.RUnlock()
	return setupData.State == string(StateReady)
}

// ────────────────────────────────────────────────────────────────────
// Config layering
// ────────────────────────────────────────────────────────────────────

type BridgeConfig struct {
	GeminiAPIKey    string `json:"gemini_api_key,omitempty"`
	GeminiModelFast string `json:"gemini_model_fast,omitempty"`
	GeminiModelPro  string `json:"gemini_model_pro,omitempty"`
	Timezone        string `json:"timezone,omitempty"`
}

var bridgeConfig BridgeConfig

func loadConfig() {
	data, err := os.ReadFile(storePath("config.json"))
	if err != nil {
		return
	}
	json.Unmarshal(data, &bridgeConfig)
}

func saveConfig() error {
	data, err := json.Marshal(bridgeConfig)
	if err != nil {
		return err
	}
	return os.WriteFile(storePath("config.json"), data, 0600)
}

// ────────────────────────────────────────────────────────────────────
// Console auth globals
// ────────────────────────────────────────────────────────────────────

var setupPassword string

type loginRateEntry struct {
	count       int
	windowStart time.Time
}

var (
	loginRateMap = make(map[string]*loginRateEntry)
	loginRateMu  sync.Mutex
)

func checkLoginRateLimit(ip string) bool {
	loginRateMu.Lock()
	defer loginRateMu.Unlock()
	now := time.Now()
	entry, ok := loginRateMap[ip]
	if !ok || now.Sub(entry.windowStart) > time.Minute {
		loginRateMap[ip] = &loginRateEntry{count: 1, windowStart: now}
		return true
	}
	if entry.count >= 5 {
		return false
	}
	entry.count++
	return true
}

func signCookie(data string) string {
	h := hmac.New(sha256.New, []byte(setupPassword))
	h.Write([]byte(data))
	return hex.EncodeToString(h.Sum(nil))
}

func validCookie(r *http.Request) bool {
	cookie, err := r.Cookie("hermes_auth")
	if err != nil {
		return false
	}
	parts := strings.SplitN(cookie.Value, ":", 2)
	if len(parts) != 2 {
		return false
	}
	expected := signCookie(parts[0])
	return hmac.Equal([]byte(parts[1]), []byte(expected))
}

func withAuth(next http.HandlerFunc) http.HandlerFunc {
	return func(w http.ResponseWriter, r *http.Request) {
		if !validCookie(r) {
			w.Header().Set("Content-Type", "application/json")
			w.WriteHeader(http.StatusUnauthorized)
			json.NewEncoder(w).Encode(map[string]string{"error": "unauthorized"})
			return
		}
		next(w, r)
	}
}

// ────────────────────────────────────────────────────────────────────
// QR image globals
// ────────────────────────────────────────────────────────────────────

var (
	currentQRDataURL string
	currentQRExpiry  time.Time
	qrMu             sync.Mutex
)

// ────────────────────────────────────────────────────────────────────
// Pairing code globals
// ────────────────────────────────────────────────────────────────────

var (
	activePairingCode string
	pairingCodeExpiry time.Time
	pairingCodeMu     sync.Mutex
)

func generatePairingCode() string {
	pairingCodeMu.Lock()
	defer pairingCodeMu.Unlock()
	code := fmt.Sprintf("MOSAIC-%04d", rand.Intn(10000))
	activePairingCode = code
	pairingCodeExpiry = time.Now().Add(10 * time.Minute)
	return code
}

// ────────────────────────────────────────────────────────────────────
// Reset confirmation globals
// ────────────────────────────────────────────────────────────────────

var (
	pendingResetChatJID string
	pendingResetExpiry  time.Time
	pendingResetMu      sync.Mutex
)

// ────────────────────────────────────────────────────────────────────
// Legacy globals (kept for existing functionality)
// ────────────────────────────────────────────────────────────────────

var ownerPhone string
var mechatJID string
var lastDisconnectAt time.Time
var disconnectNoticed bool
var recentlySentMu sync.Mutex
var recentlySent = make(map[string]time.Time)

func isRecentlySent(text string) bool {
	recentlySentMu.Lock()
	defer recentlySentMu.Unlock()
	if _, ok := recentlySent[text]; ok {
		return true
	}
	for k := range recentlySent {
		if len(k) > 80 && len(text) > 80 && k[:80] == text[:80] {
			return true
		}
	}
	return false
}

// ────────────────────────────────────────────────────────────────────
// Message types
// ────────────────────────────────────────────────────────────────────

type Message struct {
	Time      time.Time
	Sender    string
	Content   string
	IsFromMe  bool
	MediaType string
	Filename  string
}

// ────────────────────────────────────────────────────────────────────
// MessageStore
// ────────────────────────────────────────────────────────────────────

type MessageStore struct {
	db *sql.DB
}

func NewMessageStore() (*MessageStore, error) {
	if err := os.MkdirAll(storePath(""), 0755); err != nil {
		return nil, fmt.Errorf("failed to create store directory: %v", err)
	}

	db, err := sql.Open("sqlite3", "file:"+storePath("messages.db")+"?_foreign_keys=on")
	if err != nil {
		return nil, fmt.Errorf("failed to open message database: %v", err)
	}

	_, err = db.Exec(`
		CREATE TABLE IF NOT EXISTS chats (
			jid TEXT PRIMARY KEY,
			name TEXT,
			last_message_time TIMESTAMP
		);
		
		CREATE TABLE IF NOT EXISTS messages (
			id TEXT,
			chat_jid TEXT,
			sender TEXT,
			content TEXT,
			timestamp TIMESTAMP,
			is_from_me BOOLEAN,
			media_type TEXT,
			filename TEXT,
			url TEXT,
			media_key BLOB,
			file_sha256 BLOB,
			file_enc_sha256 BLOB,
			file_length INTEGER,
			PRIMARY KEY (id, chat_jid),
			FOREIGN KEY (chat_jid) REFERENCES chats(jid)
		);
	`)
	if err != nil {
		db.Close()
		return nil, fmt.Errorf("failed to create tables: %v", err)
	}

	return &MessageStore{db: db}, nil
}

func (store *MessageStore) Close() error {
	return store.db.Close()
}

func (store *MessageStore) StoreChat(jid, name string, lastMessageTime time.Time) error {
	_, err := store.db.Exec(
		"INSERT OR REPLACE INTO chats (jid, name, last_message_time) VALUES (?, ?, ?)",
		jid, name, lastMessageTime,
	)
	return err
}

func (store *MessageStore) StoreMessage(id, chatJID, sender, content string, timestamp time.Time, isFromMe bool,
	mediaType, filename, url string, mediaKey, fileSHA256, fileEncSHA256 []byte, fileLength uint64) error {
	if content == "" && mediaType == "" {
		return nil
	}

	_, err := store.db.Exec(
		`INSERT OR REPLACE INTO messages 
		(id, chat_jid, sender, content, timestamp, is_from_me, media_type, filename, url, media_key, file_sha256, file_enc_sha256, file_length) 
		VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)`,
		id, chatJID, sender, content, timestamp, isFromMe, mediaType, filename, url, mediaKey, fileSHA256, fileEncSHA256, fileLength,
	)
	return err
}

func (store *MessageStore) GetMessages(chatJID string, limit int) ([]Message, error) {
	rows, err := store.db.Query(
		"SELECT sender, content, timestamp, is_from_me, media_type, filename FROM messages WHERE chat_jid = ? ORDER BY timestamp DESC LIMIT ?",
		chatJID, limit,
	)
	if err != nil {
		return nil, err
	}
	defer rows.Close()

	var messages []Message
	for rows.Next() {
		var msg Message
		var timestamp time.Time
		err := rows.Scan(&msg.Sender, &msg.Content, &timestamp, &msg.IsFromMe, &msg.MediaType, &msg.Filename)
		if err != nil {
			return nil, err
		}
		msg.Time = timestamp
		messages = append(messages, msg)
	}

	return messages, nil
}

func (store *MessageStore) GetChats() (map[string]time.Time, error) {
	rows, err := store.db.Query("SELECT jid, last_message_time FROM chats ORDER BY last_message_time DESC")
	if err != nil {
		return nil, err
	}
	defer rows.Close()

	chats := make(map[string]time.Time)
	for rows.Next() {
		var jid string
		var lastMessageTime time.Time
		err := rows.Scan(&jid, &lastMessageTime)
		if err != nil {
			return nil, err
		}
		chats[jid] = lastMessageTime
	}

	return chats, nil
}

func (store *MessageStore) StoreMediaInfo(id, chatJID, url string, mediaKey, fileSHA256, fileEncSHA256 []byte, fileLength uint64) error {
	_, err := store.db.Exec(
		"UPDATE messages SET url = ?, media_key = ?, file_sha256 = ?, file_enc_sha256 = ?, file_length = ? WHERE id = ? AND chat_jid = ?",
		url, mediaKey, fileSHA256, fileEncSHA256, fileLength, id, chatJID,
	)
	return err
}

// LastMessageAt returns the newest message timestamp for a chat as RFC3339,
// or "" when the chat has no messages. Feeds the setup page's
// "last message N min ago" line.
func (store *MessageStore) LastMessageAt(chatJID string) string {
	var raw sql.NullString
	err := store.db.QueryRow(
		"SELECT MAX(timestamp) FROM messages WHERE chat_jid = ?", chatJID,
	).Scan(&raw)
	if err != nil || !raw.Valid || raw.String == "" {
		return ""
	}
	layouts := []string{
		time.RFC3339,
		"2006-01-02 15:04:05.999999999-07:00",
		"2006-01-02 15:04:05-07:00",
		"2006-01-02T15:04:05-07:00",
	}
	for _, layout := range layouts {
		if t, e := time.Parse(layout, raw.String); e == nil {
			return t.Format(time.RFC3339)
		}
	}
	return raw.String
}

func (store *MessageStore) GetMediaInfo(id, chatJID string) (string, string, string, []byte, []byte, []byte, uint64, error) {
	var mediaType, filename, url string
	var mediaKey, fileSHA256, fileEncSHA256 []byte
	var fileLength uint64

	err := store.db.QueryRow(
		"SELECT media_type, filename, url, media_key, file_sha256, file_enc_sha256, file_length FROM messages WHERE id = ? AND chat_jid = ?",
		id, chatJID,
	).Scan(&mediaType, &filename, &url, &mediaKey, &fileSHA256, &fileEncSHA256, &fileLength)

	return mediaType, filename, url, mediaKey, fileSHA256, fileEncSHA256, fileLength, err
}

// ────────────────────────────────────────────────────────────────────
// Utility functions
// ────────────────────────────────────────────────────────────────────

func extractTextContent(msg *waProto.Message) string {
	if msg == nil {
		return ""
	}
	if text := msg.GetConversation(); text != "" {
		return text
	} else if extendedText := msg.GetExtendedTextMessage(); extendedText != nil {
		return extendedText.GetText()
	}
	return ""
}

func extractMediaInfo(msg *waProto.Message) (mediaType string, filename string, url string, mediaKey []byte, fileSHA256 []byte, fileEncSHA256 []byte, fileLength uint64) {
	if msg == nil {
		return "", "", "", nil, nil, nil, 0
	}
	if img := msg.GetImageMessage(); img != nil {
		return "image", "image_" + time.Now().Format("20060102_150405") + ".jpg",
			img.GetURL(), img.GetMediaKey(), img.GetFileSHA256(), img.GetFileEncSHA256(), img.GetFileLength()
	}
	if vid := msg.GetVideoMessage(); vid != nil {
		return "video", "video_" + time.Now().Format("20060102_150405") + ".mp4",
			vid.GetURL(), vid.GetMediaKey(), vid.GetFileSHA256(), vid.GetFileEncSHA256(), vid.GetFileLength()
	}
	if aud := msg.GetAudioMessage(); aud != nil {
		return "audio", "audio_" + time.Now().Format("20060102_150405") + ".ogg",
			aud.GetURL(), aud.GetMediaKey(), aud.GetFileSHA256(), aud.GetFileEncSHA256(), aud.GetFileLength()
	}
	if doc := msg.GetDocumentMessage(); doc != nil {
		filename := doc.GetFileName()
		if filename == "" {
			filename = "document_" + time.Now().Format("20060102_150405")
		}
		return "document", filename,
			doc.GetURL(), doc.GetMediaKey(), doc.GetFileSHA256(), doc.GetFileEncSHA256(), doc.GetFileLength()
	}
	return "", "", "", nil, nil, nil, 0
}

func messageIsFromOwner(client *whatsmeow.Client, msg *events.Message) bool {
	if msg.Info.IsFromMe {
		return true
	}
	ownUser := client.Store.ID.User
	senderUser := msg.Info.Sender.User
	if senderUser == ownUser {
		return true
	}
	return false
}

// ────────────────────────────────────────────────────────────────────
// HTTP API types
// ────────────────────────────────────────────────────────────────────

type SendMessageResponse struct {
	Success bool   `json:"success"`
	Message string `json:"message"`
}

type SendMessageRequest struct {
	Recipient string `json:"recipient"`
	Message   string `json:"message"`
	MediaPath string `json:"media_path,omitempty"`
}

type DownloadMediaRequest struct {
	MessageID string `json:"message_id"`
	ChatJID   string `json:"chat_jid"`
}

type DownloadMediaResponse struct {
	Success  bool   `json:"success"`
	Message  string `json:"message"`
	Filename string `json:"filename,omitempty"`
	Path     string `json:"path,omitempty"`
}

// ────────────────────────────────────────────────────────────────────
// WhatsApp message sending
// ────────────────────────────────────────────────────────────────────

func sendWhatsAppMessage(client *whatsmeow.Client, recipient string, message string, mediaPath string) (bool, string) {
	if !client.IsConnected() {
		return false, "Not connected to WhatsApp"
	}

	var recipientJID types.JID
	var err error

	isJID := strings.Contains(recipient, "@")
	if isJID {
		recipientJID, err = types.ParseJID(recipient)
		if err != nil {
			return false, fmt.Sprintf("Error parsing JID: %v", err)
		}
	} else {
		recipientJID = types.JID{
			User:   recipient,
			Server: "s.whatsapp.net",
		}
	}

	msg := &waProto.Message{}

	if mediaPath != "" {
		mediaData, err := os.ReadFile(mediaPath)
		if err != nil {
			return false, fmt.Sprintf("Error reading media file: %v", err)
		}

		fileExt := strings.ToLower(mediaPath[strings.LastIndex(mediaPath, ".")+1:])
		var mediaType whatsmeow.MediaType
		var mimeType string

		switch fileExt {
		case "jpg", "jpeg":
			mediaType = whatsmeow.MediaImage
			mimeType = "image/jpeg"
		case "png":
			mediaType = whatsmeow.MediaImage
			mimeType = "image/png"
		case "gif":
			mediaType = whatsmeow.MediaImage
			mimeType = "image/gif"
		case "webp":
			mediaType = whatsmeow.MediaImage
			mimeType = "image/webp"
		case "ogg":
			mediaType = whatsmeow.MediaAudio
			mimeType = "audio/ogg; codecs=opus"
		case "mp4":
			mediaType = whatsmeow.MediaVideo
			mimeType = "video/mp4"
		case "avi":
			mediaType = whatsmeow.MediaVideo
			mimeType = "video/avi"
		case "mov":
			mediaType = whatsmeow.MediaVideo
			mimeType = "video/quicktime"
		default:
			mediaType = whatsmeow.MediaDocument
			mimeType = "application/octet-stream"
		}

		resp, err := client.Upload(context.Background(), mediaData, mediaType)
		if err != nil {
			return false, fmt.Sprintf("Error uploading media: %v", err)
		}

		fmt.Println("Media uploaded", resp)

		switch mediaType {
		case whatsmeow.MediaImage:
			msg.ImageMessage = &waProto.ImageMessage{
				Caption:       proto.String(message),
				Mimetype:      proto.String(mimeType),
				URL:           &resp.URL,
				DirectPath:    &resp.DirectPath,
				MediaKey:      resp.MediaKey,
				FileEncSHA256: resp.FileEncSHA256,
				FileSHA256:    resp.FileSHA256,
				FileLength:    &resp.FileLength,
			}
		case whatsmeow.MediaAudio:
			var seconds uint32 = 30
			var waveform []byte = nil

			if strings.Contains(mimeType, "ogg") {
				analyzedSeconds, analyzedWaveform, err := analyzeOggOpus(mediaData)
				if err == nil {
					seconds = analyzedSeconds
					waveform = analyzedWaveform
				} else {
					return false, fmt.Sprintf("Failed to analyze Ogg Opus file: %v", err)
				}
			} else {
				fmt.Printf("Not an Ogg Opus file: %s\n", mimeType)
			}

			msg.AudioMessage = &waProto.AudioMessage{
				Mimetype:      proto.String(mimeType),
				URL:           &resp.URL,
				DirectPath:    &resp.DirectPath,
				MediaKey:      resp.MediaKey,
				FileEncSHA256: resp.FileEncSHA256,
				FileSHA256:    resp.FileSHA256,
				FileLength:    &resp.FileLength,
				Seconds:       proto.Uint32(seconds),
				PTT:           proto.Bool(true),
				Waveform:      waveform,
			}
		case whatsmeow.MediaVideo:
			msg.VideoMessage = &waProto.VideoMessage{
				Caption:       proto.String(message),
				Mimetype:      proto.String(mimeType),
				URL:           &resp.URL,
				DirectPath:    &resp.DirectPath,
				MediaKey:      resp.MediaKey,
				FileEncSHA256: resp.FileEncSHA256,
				FileSHA256:    resp.FileSHA256,
				FileLength:    &resp.FileLength,
			}
		case whatsmeow.MediaDocument:
			msg.DocumentMessage = &waProto.DocumentMessage{
				Title:         proto.String(mediaPath[strings.LastIndex(mediaPath, "/")+1:]),
				FileName:      proto.String(mediaPath[strings.LastIndex(mediaPath, "/")+1:]),
				Caption:       proto.String(message),
				Mimetype:      proto.String(mimeType),
				URL:           &resp.URL,
				DirectPath:    &resp.DirectPath,
				MediaKey:      resp.MediaKey,
				FileEncSHA256: resp.FileEncSHA256,
				FileSHA256:    resp.FileSHA256,
				FileLength:    &resp.FileLength,
			}
		}
	} else {
		msg.Conversation = proto.String(message)
	}

	ctx, cancel := context.WithTimeout(context.Background(), 15*time.Second)
	defer cancel()
	_, err = client.SendMessage(ctx, recipientJID, msg)

	if err != nil {
		return false, fmt.Sprintf("Error sending message: %v", err)
	}

	return true, fmt.Sprintf("Message sent to %s", recipient)
}

// ────────────────────────────────────────────────────────────────────
// Media downloader
// ────────────────────────────────────────────────────────────────────

type MediaDownloader struct {
	URL           string
	DirectPath    string
	MediaKey      []byte
	FileLength    uint64
	FileSHA256    []byte
	FileEncSHA256 []byte
	MediaType     whatsmeow.MediaType
}

func (d *MediaDownloader) GetDirectPath() string             { return d.DirectPath }
func (d *MediaDownloader) GetURL() string                    { return d.URL }
func (d *MediaDownloader) GetMediaKey() []byte               { return d.MediaKey }
func (d *MediaDownloader) GetFileLength() uint64             { return d.FileLength }
func (d *MediaDownloader) GetFileSHA256() []byte             { return d.FileSHA256 }
func (d *MediaDownloader) GetFileEncSHA256() []byte          { return d.FileEncSHA256 }
func (d *MediaDownloader) GetMediaType() whatsmeow.MediaType { return d.MediaType }

func extractDirectPathFromURL(url string) string {
	parts := strings.SplitN(url, ".net/", 2)
	if len(parts) < 2 {
		return url
	}
	pathPart := parts[1]
	pathPart = strings.SplitN(pathPart, "?", 2)[0]
	return "/" + pathPart
}

func downloadMedia(client *whatsmeow.Client, messageStore *MessageStore, messageID, chatJID string) (bool, string, string, string, error) {
	var mediaType, filename, url string
	var mediaKey, fileSHA256, fileEncSHA256 []byte
	var fileLength uint64
	var err error

	chatDir := storePath(strings.ReplaceAll(chatJID, ":", "_"))
	localPath := ""

	mediaType, filename, url, mediaKey, fileSHA256, fileEncSHA256, fileLength, err = messageStore.GetMediaInfo(messageID, chatJID)

	if err != nil {
		err = messageStore.db.QueryRow(
			"SELECT media_type, filename FROM messages WHERE id = ? AND chat_jid = ?",
			messageID, chatJID,
		).Scan(&mediaType, &filename)

		if err != nil {
			return false, "", "", "", fmt.Errorf("failed to find message: %v", err)
		}
	}

	if mediaType == "" {
		return false, "", "", "", fmt.Errorf("not a media message")
	}

	if err := os.MkdirAll(chatDir, 0755); err != nil {
		return false, "", "", "", fmt.Errorf("failed to create chat directory: %v", err)
	}

	localPath = fmt.Sprintf("%s/%s", chatDir, filename)

	absPath, err := filepath.Abs(localPath)
	if err != nil {
		return false, "", "", "", fmt.Errorf("failed to get absolute path: %v", err)
	}

	if _, err := os.Stat(localPath); err == nil {
		return true, mediaType, filename, absPath, nil
	}

	if url == "" || len(mediaKey) == 0 || len(fileSHA256) == 0 || len(fileEncSHA256) == 0 || fileLength == 0 {
		return false, "", "", "", fmt.Errorf("incomplete media information for download")
	}

	fmt.Printf("Attempting to download media for message %s in chat %s...\n", messageID, chatJID)

	directPath := extractDirectPathFromURL(url)

	var waMediaType whatsmeow.MediaType
	switch mediaType {
	case "image":
		waMediaType = whatsmeow.MediaImage
	case "video":
		waMediaType = whatsmeow.MediaVideo
	case "audio":
		waMediaType = whatsmeow.MediaAudio
	case "document":
		waMediaType = whatsmeow.MediaDocument
	default:
		return false, "", "", "", fmt.Errorf("unsupported media type: %s", mediaType)
	}

	downloader := &MediaDownloader{
		URL:           url,
		DirectPath:    directPath,
		MediaKey:      mediaKey,
		FileLength:    fileLength,
		FileSHA256:    fileSHA256,
		FileEncSHA256: fileEncSHA256,
		MediaType:     waMediaType,
	}

	mediaData, err := client.Download(context.Background(), downloader)
	if err != nil {
		return false, "", "", "", fmt.Errorf("failed to download media: %v", err)
	}

	if err := os.WriteFile(localPath, mediaData, 0644); err != nil {
		return false, "", "", "", fmt.Errorf("failed to save media file: %v", err)
	}

	fmt.Printf("Successfully downloaded %s media to %s (%d bytes)\n", mediaType, absPath, len(mediaData))
	return true, mediaType, filename, absPath, nil
}

// ────────────────────────────────────────────────────────────────────
// Message handler
// ────────────────────────────────────────────────────────────────────

func handleMessage(client *whatsmeow.Client, messageStore *MessageStore, msg *events.Message, logger waLog.Logger) {
	chatJID := msg.Info.Chat.String()
	sender := msg.Info.Sender.User
	name := GetChatName(client, messageStore, msg.Info.Chat, chatJID, nil, sender, logger)

	err := messageStore.StoreChat(chatJID, name, msg.Info.Timestamp)
	if err != nil {
		logger.Warnf("Failed to store chat: %v", err)
	}

	content := extractTextContent(msg.Message)
	mediaType, filename, url, mediaKey, fileSHA256, fileEncSHA256, fileLength := extractMediaInfo(msg.Message)

	if content == "" && mediaType == "" {
		return
	}

	err = messageStore.StoreMessage(
		msg.Info.ID, chatJID, sender, content, msg.Info.Timestamp, msg.Info.IsFromMe,
		mediaType, filename, url, mediaKey, fileSHA256, fileEncSHA256, fileLength,
	)
	if err != nil {
		logger.Warnf("Failed to store message: %v", err)
	} else {
		timestamp := msg.Info.Timestamp.Format("2006-01-02 15:04:05")
		direction := "←"
		if msg.Info.IsFromMe {
			direction = "→"
		}
		if mediaType != "" {
			fmt.Printf("[%s] %s %s: [%s: %s] %s\n", timestamp, direction, sender, mediaType, filename, content)
		} else if content != "" {
			fmt.Printf("[%s] %s %s: %s\n", timestamp, direction, sender, content)
		}

		// ── Pairing code detection ──
		setupMu.RLock()
		state := setupData.State
		pc := activePairingCode
		pcExpiry := pairingCodeExpiry
		setupMu.RUnlock()

		if state == string(StateNeedsMeChat) && pc != "" && time.Now().Before(pcExpiry) {
			if strings.TrimSpace(content) == pc && messageIsFromOwner(client, msg) {
				setupMu.Lock()
				setupData.MeChatJID = chatJID
				setupData.MeChatName = name
				setupData.State = string(StateReady)
				setupData.UpdatedAt = time.Now().Format(time.RFC3339)
				setupData.PairedAt = time.Now().Format(time.RFC3339)
				saveSetupLocked()
				pairingCodeMu.Lock()
				activePairingCode = ""
				pairingCodeMu.Unlock()
				setupMu.Unlock()

				go func() {
					sendWhatsAppMessage(client, chatJID, "✅ *Mosaic paired to this chat.*\nThis is now your assistant chat.\nSend /help to see what I can do.", "")
				}()
				return
			}
			if strings.HasPrefix(strings.TrimSpace(content), "MOSAIC-") && !messageIsFromOwner(client, msg) {
				return
			}
		}

		// ── Gated handler spawning ──
		if isSetupReady() {
			// ── /reset chat command ──
			trimmed := strings.TrimSpace(content)
			pendingResetMu.Lock()
			isPendingReset := pendingResetChatJID == chatJID && time.Now().Before(pendingResetExpiry)
			pendingResetMu.Unlock()

			if isPendingReset && trimmed == "RESET CONFIRM" && messageIsFromOwner(client, msg) {
				pendingResetMu.Lock()
				pendingResetChatJID = ""
				pendingResetMu.Unlock()

				// Run reset inline — the client, setup state, and store paths are all accessible here
				setupMu.RLock()
				mechatForReset := setupData.MeChatJID
				setupMu.RUnlock()

				if mechatForReset != "" {
					sendWhatsAppMessage(client, mechatForReset, "⚠️ *Mosaic is being reset by its owner.*", "")
				}
				client.Logout(context.Background())

				setupMu.Lock()
				setupData.State = string(StateResetting)
				saveSetupLocked()
				setupMu.Unlock()

				time.Sleep(200 * time.Millisecond)
				os.Remove(storePath("whatsapp.db"))
				os.Remove(storePath("messages.db"))
				os.Remove(storePath("hermes.db"))
				os.Remove(storePath("mosaic.db"))
				os.Remove(storePath("session.json"))
				os.Remove(storePath("pending_messages.json"))

				keepKey := setupData.GeminiKeySet
				if !keepKey {
					os.Remove(storePath("config.json"))
				}

				setupData = SetupData{}
				if keepKey {
					setupData.GeminiKeySet = true
					setupData.State = string(StateNeedsQR)
				} else {
					setupData.State = string(StateNeedsAPIKey)
				}
				setupData.UpdatedAt = time.Now().Format(time.RFC3339)
				saveSetup()
				os.Exit(0)
				return
			}

			if trimmed == "/reset" && messageIsFromOwner(client, msg) {
				pendingResetMu.Lock()
				pendingResetChatJID = chatJID
				pendingResetExpiry = time.Now().Add(2 * time.Minute)
				pendingResetMu.Unlock()
				go func() {
					sendWhatsAppMessage(client, chatJID, "⚠️ Reply `RESET CONFIRM` within 2 minutes to wipe this instance.", "")
				}()
				return
			}

			setupMu.RLock()
			currentMechatJID := setupData.MeChatJID
			setupMu.RUnlock()

			// MeChat: never spawn handlers for slash commands — keep the assistant chat clean
			if currentMechatJID != "" && chatJID == currentMechatJID && strings.HasPrefix(content, "/") {
				logger.Infof("Skipping slash command in MeChat: %s", content)
				return
			}

			if strings.HasPrefix(content, "/") {
				senderJID := msg.Info.Sender.String()
				go func() {
					if err := postBrainEvent(chatJID, senderJID, content); err != nil {
						logger.Errorf("Brain event failed for command %s: %v", content, err)
					}
				}()
			}

			setupMu.RLock()
			currentMechatJID = setupData.MeChatJID
			setupMu.RUnlock()

			if currentMechatJID != "" && !strings.HasPrefix(content, "/") && !isRecentlySent(content) {
				chatUser := msg.Info.Chat.User
				senderUser := msg.Info.Sender.User

				isMeChat := chatUser == senderUser
				if !isMeChat && ownerPhone != "" && chatUser == ownerPhone {
					isMeChat = true
				}
				if !isMeChat && chatJID == currentMechatJID {
					isMeChat = true
				}

				fmt.Printf("[mechat-detect] chatJID=%s chatUser=%s senderUser=%s phone=%s isMeChat=%v\n",
					chatJID, chatUser, senderUser, ownerPhone, isMeChat)

				if isMeChat {
					fmt.Printf("[mechat-detect] >>> POSTING brain event for: %s\n", content)
					senderJID := msg.Info.Sender.String()
					go func() {
						if err := postBrainEvent(chatJID, senderJID, content); err != nil {
							fmt.Printf("[brain-event] ERROR: %v\n", err)
						}
					}()
				}
			}
		}
	}
}

// ────────────────────────────────────────────────────────────────────
// Brain event hook (replaces exec-python-per-message)
// ────────────────────────────────────────────────────────────────────

// brainEventURL is the Python brain's stub receiver. Override with
// BRAIN_EVENT_URL; default keeps traffic on loopback.
func brainEventURL() string {
	if u := os.Getenv("BRAIN_EVENT_URL"); u != "" {
		return u
	}
	return "http://127.0.0.1:7000/event"
}

type brainEvent struct {
	ChatJID string `json:"chat_jid"`
	Sender  string `json:"sender"`
	Content string `json:"content"`
}

func postBrainEvent(chatJID, senderJID, content string) error {
	body, err := json.Marshal(brainEvent{ChatJID: chatJID, Sender: senderJID, Content: content})
	if err != nil {
		return err
	}
	ctx, cancel := context.WithTimeout(context.Background(), 5*time.Second)
	defer cancel()
	req, err := http.NewRequestWithContext(ctx, http.MethodPost, brainEventURL(), bytes.NewReader(body))
	if err != nil {
		return err
	}
	req.Header.Set("Content-Type", "application/json")
	resp, err := http.DefaultClient.Do(req)
	if err != nil {
		return err
	}
	defer resp.Body.Close()
	io.Copy(io.Discard, io.LimitReader(resp.Body, 1<<20))
	if resp.StatusCode >= 300 {
		return fmt.Errorf("brain event status %s", resp.Status)
	}
	return nil
}

// ────────────────────────────────────────────────────────────────────
// REST API (internal listener, loopback + shared token)
// ────────────────────────────────────────────────────────────────────

// requireBridgeToken enforces the MOSAIC_BRIDGE_TOKEN shared secret on the
// send API. Fail closed: missing server-side token denies everything.
func requireBridgeToken(w http.ResponseWriter, r *http.Request) bool {
	expected := os.Getenv("MOSAIC_BRIDGE_TOKEN")
	if expected == "" {
		http.Error(w, "bridge token not configured", http.StatusInternalServerError)
		return false
	}
	got := r.Header.Get("X-Bridge-Token")
	if got == "" {
		if auth := r.Header.Get("Authorization"); strings.HasPrefix(auth, "Bearer ") {
			got = strings.TrimPrefix(auth, "Bearer ")
		}
	}
	if got == "" || !hmac.Equal([]byte(got), []byte(expected)) {
		http.Error(w, "unauthorized", http.StatusUnauthorized)
		return false
	}
	return true
}

func startInternalServer(client *whatsmeow.Client, messageStore *MessageStore, port int) {
	mux := http.NewServeMux()

	// Unauthenticated liveness for the local supervisor only; the
	// listener itself is loopback-bound (see caller).
	mux.HandleFunc("/health", func(w http.ResponseWriter, r *http.Request) {
		w.Header().Set("Content-Type", "application/json")
		json.NewEncoder(w).Encode(map[string]bool{"ok": true})
	})

	mux.HandleFunc("/api/send", func(w http.ResponseWriter, r *http.Request) {
		if r.Method != http.MethodPost {
			http.Error(w, "Method not allowed", http.StatusMethodNotAllowed)
			return
		}
		if !requireBridgeToken(w, r) {
			return
		}
		var req SendMessageRequest
		if err := json.NewDecoder(r.Body).Decode(&req); err != nil {
			http.Error(w, "Invalid request format", http.StatusBadRequest)
			return
		}
		if req.Recipient == "" {
			http.Error(w, "Recipient is required", http.StatusBadRequest)
			return
		}
		if req.Message == "" && req.MediaPath == "" {
			http.Error(w, "Message or media path is required", http.StatusBadRequest)
			return
		}
		type sendResult struct {
			success bool
			message string
		}
		done := make(chan sendResult, 1)
		go func() {
			s, m := sendWhatsAppMessage(client, req.Recipient, req.Message, req.MediaPath)
			done <- sendResult{s, m}
		}()
		var success bool
		var message string
		select {
		case result := <-done:
			success = result.success
			message = result.message
		case <-time.After(10 * time.Second):
			success = false
			message = "Send timed out"
		}
		w.Header().Set("Content-Type", "application/json")
		if !success {
			w.WriteHeader(http.StatusInternalServerError)
		}
		if req.Message != "" {
			recentlySentMu.Lock()
			recentlySent[req.Message] = time.Now()
			for k, v := range recentlySent {
				if time.Since(v) > 10*time.Minute {
					delete(recentlySent, k)
				}
			}
			recentlySentMu.Unlock()
		}
		json.NewEncoder(w).Encode(SendMessageResponse{
			Success: success,
			Message: message,
		})
	})

	mux.HandleFunc("/api/delete", func(w http.ResponseWriter, r *http.Request) {
		if r.Method != http.MethodDelete {
			http.Error(w, "Method not allowed", http.StatusMethodNotAllowed)
			return
		}
		if !requireBridgeToken(w, r) {
			return
		}
		var req struct {
			ChatJID   string `json:"chat_jid"`
			MessageID string `json:"message_id"`
		}
		if err := json.NewDecoder(r.Body).Decode(&req); err != nil {
			http.Error(w, "Invalid request format", http.StatusBadRequest)
			return
		}
		if req.ChatJID == "" || req.MessageID == "" {
			http.Error(w, "Chat JID and message ID are required", http.StatusBadRequest)
			return
		}
		chatJID, err := types.ParseJID(req.ChatJID)
		if err != nil {
			http.Error(w, "Invalid chat JID", http.StatusBadRequest)
			return
		}
		type deleteResult struct {
			success bool
			message string
		}
		done := make(chan deleteResult, 1)
		go func() {
			_, err := client.RevokeMessage(context.Background(), chatJID, types.MessageID(req.MessageID))
			if err != nil {
				done <- deleteResult{false, fmt.Sprintf("Revoke failed: %v", err)}
				return
			}
			done <- deleteResult{true, "Message revoked"}
		}()
		var success bool
		var message string
		select {
		case result := <-done:
			success = result.success
			message = result.message
		case <-time.After(10 * time.Second):
			success = false
			message = "Delete timed out"
		}
		w.Header().Set("Content-Type", "application/json")
		if !success {
			w.WriteHeader(http.StatusInternalServerError)
		}
		json.NewEncoder(w).Encode(map[string]interface{}{
			"success": success,
			"message": message,
		})
	})

	mux.HandleFunc("/api/download", func(w http.ResponseWriter, r *http.Request) {
		if r.Method != http.MethodPost {
			http.Error(w, "Method not allowed", http.StatusMethodNotAllowed)
			return
		}
		if !requireBridgeToken(w, r) {
			return
		}
		var req DownloadMediaRequest
		if err := json.NewDecoder(r.Body).Decode(&req); err != nil {
			http.Error(w, "Invalid request format", http.StatusBadRequest)
			return
		}
		if req.MessageID == "" || req.ChatJID == "" {
			http.Error(w, "Message ID and Chat JID are required", http.StatusBadRequest)
			return
		}
		success, mediaType, filename, path, err := downloadMedia(client, messageStore, req.MessageID, req.ChatJID)
		w.Header().Set("Content-Type", "application/json")
		if !success || err != nil {
			errMsg := "Unknown error"
			if err != nil {
				errMsg = err.Error()
			}
			w.WriteHeader(http.StatusInternalServerError)
			json.NewEncoder(w).Encode(DownloadMediaResponse{
				Success: false,
				Message: fmt.Sprintf("Failed to download media: %s", errMsg),
			})
			return
		}
		json.NewEncoder(w).Encode(DownloadMediaResponse{
			Success:  true,
			Message:  fmt.Sprintf("Successfully downloaded %s media", mediaType),
			Filename: filename,
			Path:     path,
		})
	})

	addr := fmt.Sprintf("127.0.0.1:%d", port)
	fmt.Printf("Starting internal API server on %s...\n", addr)
	go func() {
		if err := http.ListenAndServe(addr, mux); err != nil {
			fmt.Printf("Internal API server error: %v\n", err)
		}
	}()
}

// ────────────────────────────────────────────────────────────────────
// Setup HTTP endpoints (public listener)
// ────────────────────────────────────────────────────────────────────

func handleSetupInfo(w http.ResponseWriter, r *http.Request) {
	w.Header().Set("Content-Type", "application/json")
	resp := map[string]interface{}{
		"password_set": setupPassword != "",
	}
	json.NewEncoder(w).Encode(resp)
}

func handleHealth(w http.ResponseWriter, r *http.Request) {
	setupMu.RLock()
	state := setupData.State
	setupMu.RUnlock()
	w.Header().Set("Content-Type", "application/json")
	json.NewEncoder(w).Encode(map[string]interface{}{
		"ok":    true,
		"state": state,
	})
}

func handleSetupState(client *whatsmeow.Client, messageStore *MessageStore) http.HandlerFunc {
	return func(w http.ResponseWriter, r *http.Request) {
		setupMu.RLock()
		state := setupData.State
		ownPhone := setupData.OwnPhone
		mechatJID := setupData.MeChatJID
		mechatName := setupData.MeChatName
		geminiKeySet := setupData.GeminiKeySet
		setupMu.RUnlock()

		connected := client.IsConnected()

		resp := map[string]interface{}{
			"state":          state,
			"connected":      connected,
			"gemini_key_set": geminiKeySet,
			"uptime":         time.Since(startTime).String(),
		}
		if ownPhone != "" {
			resp["own_phone"] = ownPhone
		}
		if mechatJID != "" {
			resp["mechat_jid"] = mechatJID
		}
		if mechatName != "" {
			resp["mechat_name"] = mechatName
		}
		if mechatJID != "" && messageStore != nil {
			if last := messageStore.LastMessageAt(mechatJID); last != "" {
				resp["last_message_at"] = last
			}
		}
		if state == string(StateNeedsQR) {
			qrMu.Lock()
			resp["qr_data_url"] = currentQRDataURL
			qrMu.Unlock()
		}
		if state == string(StateNeedsMeChat) {
			pairingCodeMu.Lock()
			resp["pairing_code"] = activePairingCode
			if !pairingCodeExpiry.IsZero() {
				resp["pairing_expires_at"] = pairingCodeExpiry.Format(time.RFC3339)
			}
			pairingCodeMu.Unlock()
		}

		w.Header().Set("Content-Type", "application/json")
		json.NewEncoder(w).Encode(resp)
	}
}

func handleGeminiKey(w http.ResponseWriter, r *http.Request) {
	if r.Method != http.MethodPost {
		http.Error(w, "Method not allowed", http.StatusMethodNotAllowed)
		return
	}
	var req struct {
		APIKey string `json:"api_key"`
	}
	if err := json.NewDecoder(r.Body).Decode(&req); err != nil || req.APIKey == "" {
		w.Header().Set("Content-Type", "application/json")
		w.WriteHeader(http.StatusBadRequest)
		json.NewEncoder(w).Encode(map[string]string{"error": "api_key is required"})
		return
	}

	httpClient := &http.Client{Timeout: 10 * time.Second}
	validateReq, _ := http.NewRequest("GET", "https://generativelanguage.googleapis.com/v1beta/models?key="+req.APIKey, nil)
	resp, err := httpClient.Do(validateReq)
	if err != nil || resp.StatusCode != 200 {
		w.Header().Set("Content-Type", "application/json")
		w.WriteHeader(http.StatusBadRequest)
		json.NewEncoder(w).Encode(map[string]string{"error": "Invalid Gemini API key"})
		if resp != nil {
			resp.Body.Close()
		}
		return
	}
	resp.Body.Close()

	bridgeConfig.GeminiAPIKey = req.APIKey
	if err := saveConfig(); err != nil {
		w.Header().Set("Content-Type", "application/json")
		w.WriteHeader(http.StatusInternalServerError)
		json.NewEncoder(w).Encode(map[string]string{"error": "Failed to save config"})
		return
	}

	setupMu.Lock()
	setupData.GeminiKeySet = true
	if setupData.State == string(StateNeedsAPIKey) {
		setupData.State = string(StateNeedsQR)
	}
	setupData.UpdatedAt = time.Now().Format(time.RFC3339)
	saveSetupLocked()
	setupMu.Unlock()

	w.Header().Set("Content-Type", "application/json")
	json.NewEncoder(w).Encode(map[string]interface{}{"ok": true, "state": string(StateNeedsQR)})
}

func handlePairingRegenerate(w http.ResponseWriter, r *http.Request) {
	if r.Method != http.MethodPost {
		http.Error(w, "Method not allowed", http.StatusMethodNotAllowed)
		return
	}
	code := generatePairingCode()
	w.Header().Set("Content-Type", "application/json")
	json.NewEncoder(w).Encode(map[string]interface{}{
		"pairing_code":   code,
		"expires_at":     pairingCodeExpiry.Format(time.RFC3339),
		"expires_in_sec": 600,
	})
}

func handleRepairMechat(w http.ResponseWriter, r *http.Request) {
	if r.Method != http.MethodPost {
		http.Error(w, "Method not allowed", http.StatusMethodNotAllowed)
		return
	}
	setupMu.Lock()
	setupData.MeChatJID = ""
	setupData.MeChatName = ""
	setupData.State = string(StateNeedsMeChat)
	setupData.UpdatedAt = time.Now().Format(time.RFC3339)
	saveSetupLocked()
	code := generatePairingCode()
	setupMu.Unlock()

	w.Header().Set("Content-Type", "application/json")
	json.NewEncoder(w).Encode(map[string]interface{}{
		"ok":           true,
		"state":        string(StateNeedsMeChat),
		"pairing_code": code,
	})
}

func handleReset(client *whatsmeow.Client) http.HandlerFunc {
	return func(w http.ResponseWriter, r *http.Request) {
		if r.Method != http.MethodPost {
			http.Error(w, "Method not allowed", http.StatusMethodNotAllowed)
			return
		}
		var req struct {
			Confirm string `json:"confirm"`
			KeepKey bool   `json:"keep_key"`
		}
		json.NewDecoder(r.Body).Decode(&req)

		if req.Confirm != "RESET" {
			w.Header().Set("Content-Type", "application/json")
			w.WriteHeader(http.StatusBadRequest)
			json.NewEncoder(w).Encode(map[string]string{"error": "Type RESET to confirm"})
			return
		}

		setupMu.RLock()
		mechatJID := setupData.MeChatJID
		setupMu.RUnlock()

		if mechatJID != "" {
			sendWhatsAppMessage(client, mechatJID, "⚠️ *Mosaic is being reset by its owner.*", "")
		}

		client.Logout(context.Background())

		setupMu.Lock()
		setupData.State = string(StateResetting)
		saveSetupLocked()
		setupMu.Unlock()

		os.Remove(storePath("whatsapp.db"))
		os.Remove(storePath("messages.db"))
		os.Remove(storePath("hermes.db"))
		os.Remove(storePath("mosaic.db"))
		os.Remove(storePath("session.json"))
		os.Remove(storePath("pending_messages.json"))
		os.Remove(storePath("setup.json"))

		if !req.KeepKey {
			os.Remove(storePath("config.json"))
		}

		setupMu.Lock()
		setupData = SetupData{}
		if req.KeepKey {
			setupData.GeminiKeySet = true
			setupData.State = string(StateNeedsQR)
		} else {
			setupData.State = string(StateNeedsAPIKey)
		}
		setupData.UpdatedAt = time.Now().Format(time.RFC3339)
		saveSetupLocked()
		setupMu.Unlock()

		w.Header().Set("Content-Type", "application/json")
		json.NewEncoder(w).Encode(map[string]bool{"restarting": true})

		go func() {
			time.Sleep(500 * time.Millisecond)
			os.Exit(0)
		}()
	}
}

func handleLogin(w http.ResponseWriter, r *http.Request) {
	if r.Method != http.MethodPost {
		http.Error(w, "Method not allowed", http.StatusMethodNotAllowed)
		return
	}

	ip := r.RemoteAddr
	if fwd := r.Header.Get("X-Forwarded-For"); fwd != "" {
		ip = strings.Split(fwd, ",")[0]
	}

	if !checkLoginRateLimit(ip) {
		w.Header().Set("Content-Type", "application/json")
		w.WriteHeader(http.StatusTooManyRequests)
		json.NewEncoder(w).Encode(map[string]string{"error": "too many attempts, wait 1 minute"})
		return
	}

	var req struct {
		Password string `json:"password"`
	}
	json.NewDecoder(r.Body).Decode(&req)

	if req.Password != setupPassword {
		w.Header().Set("Content-Type", "application/json")
		w.WriteHeader(http.StatusUnauthorized)
		json.NewEncoder(w).Encode(map[string]string{"error": "invalid password"})
		return
	}

	token := fmt.Sprintf("%d", time.Now().Unix())
	sig := signCookie(token)
	cookieValue := token + ":" + sig

	http.SetCookie(w, &http.Cookie{
		Name:     "hermes_auth",
		Value:    cookieValue,
		Path:     "/",
		HttpOnly: true,
		SameSite: http.SameSiteStrictMode,
		MaxAge:   24 * 60 * 60,
	})

	w.Header().Set("Content-Type", "application/json")
	json.NewEncoder(w).Encode(map[string]bool{"ok": true})
}

func handleSetPassword(w http.ResponseWriter, r *http.Request) {
	if r.Method != http.MethodPost {
		http.Error(w, "Method not allowed", http.StatusMethodNotAllowed)
		return
	}

	if setupPassword != "" {
		w.Header().Set("Content-Type", "application/json")
		w.WriteHeader(http.StatusConflict)
		json.NewEncoder(w).Encode(map[string]string{"error": "password already set"})
		return
	}

	var req struct {
		Password string `json:"password"`
	}
	if err := json.NewDecoder(r.Body).Decode(&req); err != nil {
		http.Error(w, "bad request", http.StatusBadRequest)
		return
	}

	pw := strings.TrimSpace(req.Password)
	if len(pw) < 6 {
		w.Header().Set("Content-Type", "application/json")
		w.WriteHeader(http.StatusBadRequest)
		json.NewEncoder(w).Encode(map[string]string{"error": "password must be at least 6 characters"})
		return
	}

	setupPassword = pw
	os.WriteFile(storePath("console_password"), []byte(setupPassword), 0600)
	fmt.Println(">>> Console password has been set by first visitor <<<")

	token := fmt.Sprintf("%d", time.Now().Unix())
	sig := signCookie(token)

	http.SetCookie(w, &http.Cookie{
		Name:     "hermes_auth",
		Value:    token + ":" + sig,
		Path:     "/",
		HttpOnly: true,
		SameSite: http.SameSiteStrictMode,
		MaxAge:   24 * 60 * 60,
	})

	w.Header().Set("Content-Type", "application/json")
	json.NewEncoder(w).Encode(map[string]bool{"ok": true})
}

func serveWizard(w http.ResponseWriter, r *http.Request) {
	w.Header().Set("Content-Type", "text/html; charset=utf-8")
	w.Write(wizardHTML)
}

// ────────────────────────────────────────────────────────────────────
// Chat name helper
// ────────────────────────────────────────────────────────────────────

func GetChatName(client *whatsmeow.Client, messageStore *MessageStore, jid types.JID, chatJID string, conversation interface{}, sender string, logger waLog.Logger) string {
	var existingName string
	err := messageStore.db.QueryRow("SELECT name FROM chats WHERE jid = ?", chatJID).Scan(&existingName)
	if err == nil && existingName != "" {
		logger.Infof("Using existing chat name for %s: %s", chatJID, existingName)
		return existingName
	}

	var name string

	if jid.Server == "g.us" {
		logger.Infof("Getting name for group: %s", chatJID)

		if conversation != nil {
			var displayName, convName *string
			v := reflect.ValueOf(conversation)
			if v.Kind() == reflect.Ptr && !v.IsNil() {
				v = v.Elem()
				if displayNameField := v.FieldByName("DisplayName"); displayNameField.IsValid() && displayNameField.Kind() == reflect.Ptr && !displayNameField.IsNil() {
					dn := displayNameField.Elem().String()
					displayName = &dn
				}
				if nameField := v.FieldByName("Name"); nameField.IsValid() && nameField.Kind() == reflect.Ptr && !nameField.IsNil() {
					n := nameField.Elem().String()
					convName = &n
				}
			}
			if displayName != nil && *displayName != "" {
				name = *displayName
			} else if convName != nil && *convName != "" {
				name = *convName
			}
		}

		if name == "" {
			groupInfo, err := client.GetGroupInfo(context.Background(), jid)
			if err == nil && groupInfo.Name != "" {
				name = groupInfo.Name
			} else {
				name = fmt.Sprintf("Group %s", jid.User)
			}
		}

		logger.Infof("Using group name: %s", name)
	} else {
		logger.Infof("Getting name for contact: %s", chatJID)
		contact, err := client.Store.Contacts.GetContact(context.Background(), jid)
		if err == nil && contact.FullName != "" {
			name = contact.FullName
		} else if sender != "" {
			name = sender
		} else {
			name = jid.User
		}
		logger.Infof("Using contact name: %s", name)
	}

	return name
}

// ────────────────────────────────────────────────────────────────────
// History sync
// ────────────────────────────────────────────────────────────────────

func handleHistorySync(client *whatsmeow.Client, messageStore *MessageStore, historySync *events.HistorySync, logger waLog.Logger) {
	fmt.Printf("Received history sync event with %d conversations\n", len(historySync.Data.Conversations))

	syncedCount := 0
	for _, conversation := range historySync.Data.Conversations {
		if conversation.ID == nil {
			continue
		}
		chatJID := *conversation.ID
		jid, err := types.ParseJID(chatJID)
		if err != nil {
			logger.Warnf("Failed to parse JID %s: %v", chatJID, err)
			continue
		}

		name := GetChatName(client, messageStore, jid, chatJID, conversation, "", logger)
		messages := conversation.Messages
		if len(messages) > 0 {
			latestMsg := messages[0]
			if latestMsg == nil || latestMsg.Message == nil {
				continue
			}
			timestamp := time.Time{}
			if ts := latestMsg.Message.GetMessageTimestamp(); ts != 0 {
				timestamp = time.Unix(int64(ts), 0)
			} else {
				continue
			}
			messageStore.StoreChat(chatJID, name, timestamp)

			for _, msg := range messages {
				if msg == nil || msg.Message == nil {
					continue
				}
				var content string
				if msg.Message.Message != nil {
					if conv := msg.Message.Message.GetConversation(); conv != "" {
						content = conv
					} else if ext := msg.Message.Message.GetExtendedTextMessage(); ext != nil {
						content = ext.GetText()
					}
				}
				var mediaType, filename, url string
				var mediaKey, fileSHA256, fileEncSHA256 []byte
				var fileLength uint64

				if msg.Message.Message != nil {
					mediaType, filename, url, mediaKey, fileSHA256, fileEncSHA256, fileLength = extractMediaInfo(msg.Message.Message)
				}

				logger.Infof("Message content: %v, Media Type: %v", content, mediaType)

				if content == "" && mediaType == "" {
					continue
				}

				var sender string
				isFromMe := false
				if msg.Message.Key != nil {
					if msg.Message.Key.FromMe != nil {
						isFromMe = *msg.Message.Key.FromMe
					}
					if !isFromMe && msg.Message.Key.Participant != nil && *msg.Message.Key.Participant != "" {
						sender = *msg.Message.Key.Participant
					} else if isFromMe {
						sender = client.Store.ID.User
					} else {
						sender = jid.User
					}
				} else {
					sender = jid.User
				}

				msgID := ""
				if msg.Message.Key != nil && msg.Message.Key.ID != nil {
					msgID = *msg.Message.Key.ID
				}

				timestamp := time.Time{}
				if ts := msg.Message.GetMessageTimestamp(); ts != 0 {
					timestamp = time.Unix(int64(ts), 0)
				} else {
					continue
				}

				err = messageStore.StoreMessage(
					msgID, chatJID, sender, content, timestamp, isFromMe,
					mediaType, filename, url, mediaKey, fileSHA256, fileEncSHA256, fileLength,
				)
				if err != nil {
					logger.Warnf("Failed to store history message: %v", err)
				} else {
					syncedCount++
					if mediaType != "" {
						logger.Infof("Stored message: [%s] %s -> %s: [%s: %s] %s",
							timestamp.Format("2006-01-02 15:04:05"), sender, chatJID, mediaType, filename, content)
					} else {
						logger.Infof("Stored message: [%s] %s -> %s: %s",
							timestamp.Format("2006-01-02 15:04:05"), sender, chatJID, content)
					}
				}
			}
		}
	}

	fmt.Printf("History sync complete. Stored %d messages.\n", syncedCount)
}

func requestHistorySync(client *whatsmeow.Client) {
	if client == nil {
		fmt.Println("Client is not initialized. Cannot request history sync.")
		return
	}
	if !client.IsConnected() {
		fmt.Println("Client is not connected. Please ensure you are connected to WhatsApp first.")
		return
	}
	if client.Store.ID == nil {
		fmt.Println("Client is not logged in. Please scan the QR code first.")
		return
	}
	historyMsg := client.BuildHistorySyncRequest(nil, 100)
	if historyMsg == nil {
		fmt.Println("Failed to build history sync request.")
		return
	}
	_, err := client.SendMessage(context.Background(), types.JID{
		Server: "s.whatsapp.net",
		User:   "status",
	}, historyMsg)
	if err != nil {
		fmt.Printf("Failed to request history sync: %v\n", err)
	} else {
		fmt.Println("History sync requested. Waiting for server response...")
	}
}

// ────────────────────────────────────────────────────────────────────
// Audio analysis
// ────────────────────────────────────────────────────────────────────

func analyzeOggOpus(data []byte) (duration uint32, waveform []byte, err error) {
	if len(data) < 4 || string(data[0:4]) != "OggS" {
		return 0, nil, fmt.Errorf("not a valid Ogg file (missing OggS signature)")
	}

	var lastGranule uint64
	var sampleRate uint32 = 48000
	var preSkip uint16 = 0
	var foundOpusHead bool

	for i := 0; i < len(data); {
		if i+27 >= len(data) {
			break
		}
		if string(data[i:i+4]) != "OggS" {
			i++
			continue
		}
		granulePos := binary.LittleEndian.Uint64(data[i+6 : i+14])
		pageSeqNum := binary.LittleEndian.Uint32(data[i+18 : i+22])
		numSegments := int(data[i+26])

		if i+27+numSegments >= len(data) {
			break
		}
		segmentTable := data[i+27 : i+27+numSegments]
		pageSize := 27 + numSegments
		for _, segLen := range segmentTable {
			pageSize += int(segLen)
		}

		if !foundOpusHead && pageSeqNum <= 1 {
			pageData := data[i : i+pageSize]
			headPos := bytes.Index(pageData, []byte("OpusHead"))
			if headPos >= 0 && headPos+12 < len(pageData) {
				headPos += 8
				if headPos+12 <= len(pageData) {
					preSkip = binary.LittleEndian.Uint16(pageData[headPos+10 : headPos+12])
					sampleRate = binary.LittleEndian.Uint32(pageData[headPos+12 : headPos+16])
					foundOpusHead = true
					fmt.Printf("Found OpusHead: sampleRate=%d, preSkip=%d\n", sampleRate, preSkip)
				}
			}
		}

		if granulePos != 0 {
			lastGranule = granulePos
		}
		i += pageSize
	}

	if !foundOpusHead {
		fmt.Println("Warning: OpusHead not found, using default values")
	}

	if lastGranule > 0 {
		durationSeconds := float64(lastGranule-uint64(preSkip)) / float64(sampleRate)
		duration = uint32(math.Ceil(durationSeconds))
		fmt.Printf("Calculated Opus duration from granule: %f seconds (lastGranule=%d)\n",
			durationSeconds, lastGranule)
	} else {
		fmt.Println("Warning: No valid granule position found, using estimation")
		durationEstimate := float64(len(data)) / 2000.0
		duration = uint32(durationEstimate)
	}

	if duration < 1 {
		duration = 1
	} else if duration > 300 {
		duration = 300
	}

	waveform = placeholderWaveform(duration)

	fmt.Printf("Ogg Opus analysis: size=%d bytes, calculated duration=%d sec, waveform=%d bytes\n",
		len(data), duration, len(waveform))

	return duration, waveform, nil
}

func minInt(x, y int) int {
	if x < y {
		return x
	}
	return y
}

func placeholderWaveform(duration uint32) []byte {
	const waveformLength = 64
	waveform := make([]byte, waveformLength)

	rand.Seed(int64(duration))

	baseAmplitude := 35.0
	frequencyFactor := float64(minInt(int(duration), 120)) / 30.0

	for i := range waveform {
		pos := float64(i) / float64(waveformLength)
		val := baseAmplitude * math.Sin(pos*math.Pi*frequencyFactor*8)
		val += (baseAmplitude / 2) * math.Sin(pos*math.Pi*frequencyFactor*16)
		val += (rand.Float64() - 0.5) * 15

		fadeInOut := math.Sin(pos * math.Pi)
		val = val * (0.7 + 0.3*fadeInOut)

		val = val + 50

		if val < 0 {
			val = 0
		} else if val > 100 {
			val = 100
		}

		waveform[i] = byte(val)
	}

	return waveform
}

// ────────────────────────────────────────────────────────────────────
// Password generation
// ────────────────────────────────────────────────────────────────────

func generatePassword() string {
	const charset = "abcdefghijklmnopqrstuvwxyzABCDEFGHIJKLMNOPQRSTUVWXYZ0123456789"
	b := make([]byte, 12)
	if _, err := io.ReadFull(cryptorand.Reader, b); err != nil {
		panic(err)
	}
	for i := range b {
		b[i] = charset[int(b[i])%len(charset)]
	}
	return string(b)
}

// ────────────────────────────────────────────────────────────────────
// main
// ────────────────────────────────────────────────────────────────────

func main() {
	startTime = time.Now()

	os.MkdirAll(storePath(""), 0755)

	logger := waLog.Stdout("Client", "INFO", true)
	logger.Infof("Starting Mosaic bridge...")

	ownerPhone = os.Getenv("OWNER_PHONE_NUMBER")
	if ownerPhone != "" {
		logger.Infof("Owner phone set: %s", ownerPhone)
	}

	envMechatJID := os.Getenv("MECHAT_JID")
	if envMechatJID != "" {
		logger.Infof("MeChat JID override from env: %s", envMechatJID)
	}

	// ── Setup password ──
	setupPassword = os.Getenv("SETUP_PASSWORD")
	if setupPassword == "" {
		if data, err := os.ReadFile(storePath("console_password")); err == nil && len(data) > 0 {
			setupPassword = strings.TrimSpace(string(data))
		}
	}
	if setupPassword != "" {
		fmt.Printf("\n>>> Mosaic console is password-protected <<<\n\n")
	} else {
		fmt.Printf("\n>>> Mosaic console: no password yet — first visitor creates one <<<\n\n")
	}

	// ── Load state ──
	loadSetup()

	// ── Config layering ──
	loadConfig()

	geminiEnv := os.Getenv("GEMINI_API_KEY")
	if geminiEnv != "" {
		bridgeConfig.GeminiAPIKey = geminiEnv
		saveConfig()
		setupMu.Lock()
		setupData.GeminiKeySet = true
		setupMu.Unlock()
	}
	if bridgeConfig.GeminiAPIKey == "" && setupData.GeminiKeySet {
		setupMu.Lock()
		setupData.GeminiKeySet = false
		setupMu.Unlock()
	}

	// ── Boot resolution ──
	setupMu.Lock()
	if setupData.GeminiKeySet && setupData.State == string(StateNeedsAPIKey) {
		setupData.State = string(StateNeedsQR)
	}
	setupMu.Unlock()

	// ── Computed mechatJID ──
	setupMu.RLock()
	mechatJID = setupData.MeChatJID
	setupMu.RUnlock()
	if envMechatJID != "" {
		mechatJID = envMechatJID
	}
	if mechatJID != "" {
		logger.Infof("MeChat JID: %s", mechatJID)
	}

	// ── Database ──
	dbLog := waLog.Stdout("Database", "INFO", true)

	if err := os.MkdirAll(storePath(""), 0755); err != nil {
		logger.Errorf("Failed to create store directory: %v", err)
		return
	}

	container, err := sqlstore.New(context.Background(), "sqlite3", "file:"+storePath("whatsapp.db")+"?_foreign_keys=on", dbLog)
	if err != nil {
		logger.Errorf("Failed to connect to database: %v", err)
		return
	}

	deviceStore, err := container.GetFirstDevice(context.Background())
	hasDevice := err == nil
	if err != nil {
		if err == sql.ErrNoRows {
			deviceStore = container.NewDevice()
			logger.Infof("Created new device")
		} else {
			logger.Errorf("Failed to get device: %v", err)
			return
		}
	}

	if hasDevice {
		setupMu.Lock()
		newState := resolveStateLocked()
		if newState == StateNeedsQR || newState == StateNeedsMeChat || newState == StateReady {
			if setupData.State == string(StateNeedsQR) {
				setupData.State = string(StateNeedsMeChat)
			}
			if setupData.State == string(StateNeedsMeChat) && setupData.MeChatJID != "" {
				setupData.State = string(StateReady)
			}
			if setupData.State == string(StateNeedsMeChat) && activePairingCode == "" {
				generatePairingCode()
			}
		}
		setupData.UpdatedAt = time.Now().Format(time.RFC3339)
		saveSetupLocked()
		setupMu.Unlock()
		mechatJID = setupData.MeChatJID
		if envMechatJID != "" {
			mechatJID = envMechatJID
		}
	}

	client := whatsmeow.NewClient(deviceStore, logger)
	if client == nil {
		logger.Errorf("Failed to create WhatsApp client")
		return
	}

	messageStore, err := NewMessageStore()
	if err != nil {
		logger.Errorf("Failed to initialize message store: %v", err)
		return
	}
	defer messageStore.Close()

	// ── Media downloads directory ──
	os.MkdirAll(storePath("media"), 0755)

	// ── Event handlers ──
	client.AddEventHandler(func(evt interface{}) {
		switch v := evt.(type) {
		case *events.Message:
			handleMessage(client, messageStore, v, logger)

		case *events.HistorySync:
			handleHistorySync(client, messageStore, v, logger)

		case *events.Connected:
			logger.Infof("Connected to WhatsApp")
			if disconnectNoticed {
				disconnectNoticed = false
				fmt.Printf("[bridge] reconnect notice: back online after disconnect at %s\n",
					lastDisconnectAt.Format(time.RFC3339))
			}

		case *events.Disconnected:
			lastDisconnectAt = time.Now()
			disconnectNoticed = true
			fmt.Printf("[bridge] disconnect notice: websocket closed at %s, retrying\n",
				lastDisconnectAt.Format(time.RFC3339))

		case *events.LoggedOut:
			logger.Warnf("Device logged out")
			setupMu.Lock()
			setupData.State = string(StateNeedsQR)
			setupData.MeChatJID = ""
			setupData.MeChatName = ""
			saveSetupLocked()
			setupMu.Unlock()
		}
	})

	// ── Start HTTP servers BEFORE connection so wizard is available during QR ──
	publicPort := 8080
	for _, envKey := range []string{"BRIDGE_PORT", "PORT"} {
		if p := os.Getenv(envKey); p != "" {
			if n, err := strconv.Atoi(p); err == nil && n > 0 {
				publicPort = n
				break
			}
		}
	}

	publicMux := http.NewServeMux()
	publicMux.HandleFunc("/", serveWizard)
	publicMux.HandleFunc("/health", handleHealth)
	publicMux.HandleFunc("/setup/login", handleLogin)
	publicMux.HandleFunc("/setup/info", handleSetupInfo)
	publicMux.HandleFunc("/setup/set-password", handleSetPassword)
	publicMux.HandleFunc("/setup/state", withAuth(handleSetupState(client, messageStore)))
	publicMux.HandleFunc("/setup/gemini-key", withAuth(handleGeminiKey))
	publicMux.HandleFunc("/setup/pairing/regenerate", withAuth(handlePairingRegenerate))
	publicMux.HandleFunc("/setup/repair-mechat", withAuth(handleRepairMechat))
	publicMux.HandleFunc("/setup/reset", withAuth(handleReset(client)))

	go func() {
		addr := fmt.Sprintf(":%d", publicPort)
		fmt.Printf("Starting public server on %s...\n", addr)
		if err := http.ListenAndServe(addr, publicMux); err != nil {
			fmt.Printf("Public server error: %v\n", err)
		}
	}()

	internalPort := 8081
	if p := os.Getenv("BRIDGE_INTERNAL_PORT"); p != "" {
		if n, err := strconv.Atoi(p); err == nil && n > 0 {
			internalPort = n
		}
	}
	startInternalServer(client, messageStore, internalPort)

	// ── Connection ──
	connected := make(chan bool, 1)
	if client.Store.ID == nil {
		// Only advance past NEEDS_API_KEY if gemini key is already set
		setupMu.Lock()
		if setupData.State == string(StateNeedsAPIKey) && setupData.GeminiKeySet {
			setupData.State = string(StateNeedsQR)
		}
		saveSetupLocked()
		setupMu.Unlock()

		qrChan, _ := client.GetQRChannel(context.Background())
		err = client.Connect()
		if err != nil {
			logger.Errorf("Failed to connect: %v", err)
			return
		}

		for evt := range qrChan {
			if evt.Event == "code" {
				png, err := qrcode.Encode(evt.Code, qrcode.Medium, 256)
				if err == nil {
					qrMu.Lock()
					currentQRDataURL = "data:image/png;base64," + base64.StdEncoding.EncodeToString(png)
					currentQRExpiry = time.Now().Add(2 * time.Minute)
					qrMu.Unlock()
				}
				fmt.Println("\nScan this QR code with your WhatsApp app:")
				qrterminal.GenerateHalfBlock(evt.Code, qrterminal.L, os.Stdout)
			} else if evt.Event == "success" {
				qrMu.Lock()
				currentQRDataURL = ""
				qrMu.Unlock()
				connected <- true
				break
			}
		}

		select {
		case <-connected:
			fmt.Println("\nSuccessfully connected and authenticated!")
		case <-time.After(30 * time.Minute):
			logger.Errorf("Timeout waiting for QR code scan (30 min)")
			return
		}
	} else {
		err = client.Connect()
		if err != nil {
			logger.Errorf("Failed to connect: %v", err)
			return
		}
		connected <- true
	}

	for i := 0; i < 30; i++ {
		if client.IsConnected() {
			break
		}
		time.Sleep(1 * time.Second)
	}

	if !client.IsConnected() {
		logger.Errorf("Failed to establish stable connection after 30s")
		return
	}

	fmt.Println("\n✓ Connected to WhatsApp! Type 'help' for commands.")

	// ── Post-connection state update ──
	// Self-chat only: the owner's own chat is always <PN>@s.whatsapp.net,
	// so pairing completes on connect with no manual step and no group.
	setupMu.Lock()
	setupData.OwnPhone = client.Store.ID.User
	setupData.OwnJID = client.Store.ID.String()
	if setupData.MeChatJID == "" {
		setupData.MeChatJID = client.Store.ID.User + "@s.whatsapp.net"
		setupData.MeChatName = "Me"
		setupData.PairedAt = time.Now().Format(time.RFC3339)
	}
	if setupData.State == string(StateNeedsQR) || setupData.State == string(StateNeedsMeChat) {
		setupData.State = string(StateReady)
	}
	setupData.UpdatedAt = time.Now().Format(time.RFC3339)
	saveSetupLocked()
	setupMu.Unlock()

	mechatJID = setupData.MeChatJID
	if envMechatJID != "" {
		mechatJID = envMechatJID
	}

	// ── Signal handling ──
	exitChan := make(chan os.Signal, 1)
	signal.Notify(exitChan, syscall.SIGINT, syscall.SIGTERM)

	fmt.Println("Mosaic bridge is running. Press Ctrl+C to disconnect and exit.")
	<-exitChan

	fmt.Println("Disconnecting...")
	client.Disconnect()
}
