// Package store: sqlite persistence (pure Go driver, same schema as the Python version).
package store

import (
	"database/sql"
	"fmt"
	"time"

	_ "modernc.org/sqlite"
)

type DB struct{ *sql.DB }

type Account struct {
	ID        int64
	Address   string
	Provider  string
	AuthCode  string // plaintext in-process; encrypted at rest if EncryptKey set
	Status    string
	LastError string
}

type Folder struct {
	ID          int64
	AccountID   int64
	Name        string // raw modified-UTF7 IMAP name
	NameDecoded string
	UIDValidity int64
	LastSeenUID uint32
}

type Message struct {
	ID          int64
	AccountID   int64
	FolderID    int64
	UID         uint32
	MessageID   string
	Subject     string
	FromAddr    string // "Name <addr>" flattened
	Date        time.Time
	InternalDate time.Time
	Size        int64
	Seen        bool
	Snippet     string
	TextBody    string
	HTMLBody    string
	Raw         []byte
	BodyFetched bool
}

type ObjectRef struct {
	ID          int64
	MessageID   int64
	AccountID   int64
	Provider    string
	Bucket      string
	Key         string
	Region      string
	URL         string
	Presigned   bool
	Source      string // regex | llm
	ExpectedMD5 string
}

type Judgment struct {
	MessageID       int64
	Category        string
	CategoryConf    float64
	StorageDelivery float64
	ActionRequired  int
	Model           string
}

type Store struct {
	ID        int64
	Name      string
	Provider  string
	Bucket    string
	Region    string
	Endpoint  string
	AccessKey string
	SecretKey string
	Enabled   bool
}

type PullRecord struct {
	ID         int64
	MessageID  int64
	Provider   string
	Bucket     string
	RemoteKey  string
	LocalPath  string
	Size       int64
	Status     string // done | failed
	Error      string
	PulledAt   time.Time
}

func Open(path string) (*DB, error) {
	db, err := sql.Open("sqlite", path+"?_pragma=journal_mode(WAL)&_pragma=busy_timeout(5000)&_pragma=foreign_keys(1)")
	if err != nil {
		return nil, err
	}
	d := &DB{db}
	return d, d.migrate()
}

func (d *DB) migrate() error {
	_, err := d.Exec(`
CREATE TABLE IF NOT EXISTS accounts(
  id INTEGER PRIMARY KEY AUTOINCREMENT,
  address TEXT UNIQUE NOT NULL,
  provider TEXT DEFAULT '163',
  auth_code TEXT NOT NULL,
  status TEXT DEFAULT 'active',
  last_error TEXT DEFAULT '',
  last_sync_at DATETIME,
  created_at DATETIME DEFAULT CURRENT_TIMESTAMP);
CREATE TABLE IF NOT EXISTS folders(
  id INTEGER PRIMARY KEY AUTOINCREMENT,
  account_id INTEGER NOT NULL REFERENCES accounts(id),
  name TEXT NOT NULL,
  name_decoded TEXT DEFAULT '',
  uidvalidity INTEGER DEFAULT 0,
  last_seen_uid INTEGER DEFAULT 0,
  UNIQUE(account_id, name));
CREATE TABLE IF NOT EXISTS messages(
  id INTEGER PRIMARY KEY AUTOINCREMENT,
  account_id INTEGER NOT NULL REFERENCES accounts(id),
  folder_id INTEGER NOT NULL REFERENCES folders(id),
  uid INTEGER NOT NULL,
  message_id TEXT DEFAULT '',
  subject TEXT DEFAULT '',
  from_addr TEXT DEFAULT '',
  date DATETIME,
  internal_date DATETIME,
  size INTEGER DEFAULT 0,
  seen INTEGER DEFAULT 0,
  snippet TEXT DEFAULT '',
  text_body TEXT,
  html_body TEXT,
  raw BLOB,
  body_fetched INTEGER DEFAULT 0,
  created_at DATETIME DEFAULT CURRENT_TIMESTAMP,
  UNIQUE(account_id, folder_id, uid));
CREATE TABLE IF NOT EXISTS object_refs(
  id INTEGER PRIMARY KEY AUTOINCREMENT,
  message_id INTEGER NOT NULL REFERENCES messages(id),
  account_id INTEGER NOT NULL,
  provider TEXT NOT NULL,
  bucket TEXT NOT NULL,
  key_ TEXT DEFAULT '',
  region TEXT DEFAULT '',
  url TEXT NOT NULL,
  presigned INTEGER DEFAULT 0,
  source TEXT DEFAULT 'regex',
  expected_md5 TEXT DEFAULT '',
  UNIQUE(message_id, url));
CREATE TABLE IF NOT EXISTS judgments(
  id INTEGER PRIMARY KEY AUTOINCREMENT,
  message_id INTEGER UNIQUE NOT NULL REFERENCES messages(id),
  category TEXT DEFAULT '',
  category_conf REAL DEFAULT 0,
  storage_delivery REAL DEFAULT 0,
  action_required INTEGER DEFAULT 0,
  model TEXT DEFAULT '',
  created_at DATETIME DEFAULT CURRENT_TIMESTAMP);
CREATE TABLE IF NOT EXISTS stores(
  id INTEGER PRIMARY KEY AUTOINCREMENT,
  name TEXT UNIQUE NOT NULL,
  provider TEXT NOT NULL,
  bucket TEXT NOT NULL,
  region TEXT DEFAULT '',
  endpoint TEXT DEFAULT '',
  access_key TEXT NOT NULL,
  secret_key TEXT NOT NULL,
  enabled INTEGER DEFAULT 1);
CREATE TABLE IF NOT EXISTS pull_records(
  id INTEGER PRIMARY KEY AUTOINCREMENT,
  message_id INTEGER NOT NULL REFERENCES messages(id),
  provider TEXT DEFAULT '',
  bucket TEXT DEFAULT '',
  remote_key TEXT NOT NULL,
  local_path TEXT DEFAULT '',
  size INTEGER DEFAULT 0,
  status TEXT DEFAULT 'done',
  error TEXT DEFAULT '',
  pulled_at DATETIME DEFAULT CURRENT_TIMESTAMP,
  UNIQUE(message_id, remote_key));
CREATE INDEX IF NOT EXISTS ix_messages_folder ON messages(folder_id, uid);
CREATE INDEX IF NOT EXISTS ix_refs_msg ON object_refs(message_id);
CREATE INDEX IF NOT EXISTS ix_pulls_msg ON pull_records(message_id);`)
	return err
}

// ---- accounts / folders ----

func (d *DB) UpsertAccount(address, provider, authCode string) (int64, error) {
	_, err := d.Exec(`INSERT INTO accounts(address,provider,auth_code) VALUES(?,?,?)
		ON CONFLICT(address) DO UPDATE SET provider=excluded.provider, auth_code=excluded.auth_code`,
		address, provider, authCode)
	if err != nil {
		return 0, err
	}
	var id int64
	err = d.QueryRow(`SELECT id FROM accounts WHERE address=?`, address).Scan(&id)
	return id, err
}

func (d *DB) Accounts() ([]Account, error) {
	rows, err := d.Query(`SELECT id,address,provider,auth_code,status,last_error FROM accounts WHERE provider != 'webhook'`)
	if err != nil {
		return nil, err
	}
	defer rows.Close()
	var out []Account
	for rows.Next() {
		var a Account
		if err := rows.Scan(&a.ID, &a.Address, &a.Provider, &a.AuthCode, &a.Status, &a.LastError); err != nil {
			return nil, err
		}
		out = append(out, a)
	}
	return out, rows.Err()
}

func (d *DB) UpsertFolder(accountID int64, name, decoded string, uidvalidity int64) (int64, error) {
	_, err := d.Exec(`INSERT INTO folders(account_id,name,name_decoded,uidvalidity) VALUES(?,?,?,?)
		ON CONFLICT(account_id,name) DO UPDATE SET name_decoded=excluded.name_decoded, uidvalidity=excluded.uidvalidity`,
		accountID, name, decoded, uidvalidity)
	if err != nil {
		return 0, err
	}
	var id int64
	err = d.QueryRow(`SELECT id FROM folders WHERE account_id=? AND name=?`, accountID, name).Scan(&id)
	return id, err
}

func (d *DB) FolderByID(id int64) (*Folder, error) {
	var f Folder
	err := d.QueryRow(`SELECT id,account_id,name,name_decoded,uidvalidity,last_seen_uid FROM folders WHERE id=?`, id).
		Scan(&f.ID, &f.AccountID, &f.Name, &f.NameDecoded, &f.UIDValidity, &f.LastSeenUID)
	return &f, err
}

func (d *DB) Folders(accountID int64) ([]Folder, error) {
	rows, err := d.Query(`SELECT id,account_id,name,name_decoded,uidvalidity,last_seen_uid FROM folders WHERE account_id=?`, accountID)
	if err != nil {
		return nil, err
	}
	defer rows.Close()
	var out []Folder
	for rows.Next() {
		var f Folder
		if err := rows.Scan(&f.ID, &f.AccountID, &f.Name, &f.NameDecoded, &f.UIDValidity, &f.LastSeenUID); err != nil {
			return nil, err
		}
		out = append(out, f)
	}
	return out, rows.Err()
}

func (d *DB) WipeFolder(folderID int64) error {
	_, err := d.Exec(`DELETE FROM pull_records WHERE message_id IN (SELECT id FROM messages WHERE folder_id=?)`, folderID)
	if err != nil { return err }
	_, err = d.Exec(`DELETE FROM object_refs WHERE message_id IN (SELECT id FROM messages WHERE folder_id=?)`, folderID)
	if err != nil { return err }
	_, err = d.Exec(`DELETE FROM judgments WHERE message_id IN (SELECT id FROM messages WHERE folder_id=?)`, folderID)
	if err != nil { return err }
	_, err = d.Exec(`DELETE FROM messages WHERE folder_id=?`, folderID)
	return err
}

func (d *DB) InsertMessage(m *Message) (int64, error) {
	res, err := d.Exec(`INSERT INTO messages(account_id,folder_id,uid,message_id,subject,from_addr,date,internal_date,size,seen,snippet,raw,text_body,html_body,body_fetched)
		VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)
		ON CONFLICT(account_id,folder_id,uid) DO NOTHING`,
		m.AccountID, m.FolderID, m.UID, m.MessageID, m.Subject, m.FromAddr, nilTime(m.Date), nilTime(m.InternalDate),
		m.Size, m.Seen, m.Snippet, m.Raw, nilStr(m.TextBody), nilStr(m.HTMLBody), m.BodyFetched)
	if err != nil {
		return 0, err
	}
	if n, _ := res.RowsAffected(); n == 0 {
		var id int64
		err = d.QueryRow(`SELECT id FROM messages WHERE account_id=? AND folder_id=? AND uid=?`, m.AccountID, m.FolderID, m.UID).Scan(&id)
		return id, err
	}
	return res.LastInsertId()
}

func (d *DB) UpdateFolderCursor(folderID int64, lastUID uint32, uidvalidity int64) error {
	_, err := d.Exec(`UPDATE folders SET last_seen_uid=?, uidvalidity=? WHERE id=?`, lastUID, uidvalidity, folderID)
	return err
}

func (d *DB) SetAccountError(id int64, msg string) {
	d.Exec(`UPDATE accounts SET last_error=?, last_sync_at=CURRENT_TIMESTAMP WHERE id=?`, msg, id)
}

// ---- message body / refs / judgments ----

func (d *DB) Message(id int64) (*Message, error) {
	var m Message
	var date, idate sql.NullTime
	var tb, hb sql.NullString
	err := d.QueryRow(`SELECT id,account_id,folder_id,uid,message_id,subject,from_addr,date,internal_date,size,seen,snippet,text_body,html_body,raw,body_fetched
		FROM messages WHERE id=?`, id).Scan(&m.ID, &m.AccountID, &m.FolderID, &m.UID, &m.MessageID,
		&m.Subject, &m.FromAddr, &date, &idate, &m.Size, &m.Seen, &m.Snippet, &tb, &hb, &m.Raw, &m.BodyFetched)
	if err != nil {
		return nil, err
	}
	m.Date, m.InternalDate = date.Time, idate.Time
	m.TextBody, m.HTMLBody = tb.String, hb.String
	return &m, nil
}

func (d *DB) SaveBody(m *Message) error {
	_, err := d.Exec(`UPDATE messages SET raw=?,text_body=?,html_body=?,snippet=?,size=?,body_fetched=1 WHERE id=?`,
		m.Raw, nilStr(m.TextBody), nilStr(m.HTMLBody), m.Snippet, m.Size, m.ID)
	return err
}

func (d *DB) ReplaceRefs(messageID int64, refs []ObjectRef) error {
	tx, err := d.Begin()
	if err != nil {
		return err
	}
	defer tx.Rollback()
	if _, err := tx.Exec(`DELETE FROM object_refs WHERE message_id=?`, messageID); err != nil {
		return err
	}
	for _, r := range refs {
		if _, err := tx.Exec(`INSERT INTO object_refs(message_id,account_id,provider,bucket,key_,region,url,presigned,source,expected_md5)
			VALUES(?,?,?,?,?,?,?,?,?,?)`,
			messageID, r.AccountID, r.Provider, r.Bucket, r.Key, r.Region, r.URL, r.Presigned, r.Source, r.ExpectedMD5); err != nil {
			return err
		}
	}
	return tx.Commit()
}

func (d *DB) Refs(messageID int64) ([]ObjectRef, error) {
	rows, err := d.Query(`SELECT id,message_id,account_id,provider,bucket,key_,region,url,presigned,source,expected_md5
		FROM object_refs WHERE message_id=? ORDER BY id`, messageID)
	if err != nil {
		return nil, err
	}
	defer rows.Close()
	var out []ObjectRef
	for rows.Next() {
		var r ObjectRef
		if err := rows.Scan(&r.ID, &r.MessageID, &r.AccountID, &r.Provider, &r.Bucket, &r.Key,
			&r.Region, &r.URL, &r.Presigned, &r.Source, &r.ExpectedMD5); err != nil {
			return nil, err
		}
		out = append(out, r)
	}
	return out, rows.Err()
}

func (d *DB) SaveJudgment(j *Judgment) error {
	_, err := d.Exec(`INSERT INTO judgments(message_id,category,category_conf,storage_delivery,action_required,model)
		VALUES(?,?,?,?,?,?)
		ON CONFLICT(message_id) DO UPDATE SET category=excluded.category, category_conf=excluded.category_conf,
		storage_delivery=excluded.storage_delivery, action_required=excluded.action_required, model=excluded.model`,
		j.MessageID, j.Category, j.CategoryConf, j.StorageDelivery, j.ActionRequired, j.Model)
	return err
}

func (d *DB) Judgment(messageID int64) (*Judgment, error) {
	var j Judgment
	err := d.QueryRow(`SELECT message_id,category,category_conf,storage_delivery,action_required,model FROM judgments WHERE message_id=?`,
		messageID).Scan(&j.MessageID, &j.Category, &j.CategoryConf, &j.StorageDelivery, &j.ActionRequired, &j.Model)
	return &j, err
}

func (d *DB) PendingMessageIDs(limit int) ([]int64, error) {
	rows, err := d.Query(`SELECT m.id FROM messages m
		LEFT JOIN judgments j ON j.message_id = m.id
		WHERE m.body_fetched = 0 OR j.id IS NULL
		ORDER BY m.id LIMIT ?`, limit)
	if err != nil {
		return nil, err
	}
	defer rows.Close()
	var out []int64
	for rows.Next() {
		var id int64
		rows.Scan(&id)
		out = append(out, id)
	}
	return out, rows.Err()
}

// ---- stores / pulls ----

func (d *DB) FindStore(provider, bucket string) (*Store, error) {
	var s Store
	err := d.QueryRow(`SELECT id,name,provider,bucket,region,endpoint,access_key,secret_key,enabled
		FROM stores WHERE provider=? AND bucket=? AND enabled=1`, provider, bucket).
		Scan(&s.ID, &s.Name, &s.Provider, &s.Bucket, &s.Region, &s.Endpoint, &s.AccessKey, &s.SecretKey, &s.Enabled)
	return &s, err
}

func (d *DB) UpsertPullRecord(r *PullRecord) error {
	_, err := d.Exec(`INSERT INTO pull_records(message_id,provider,bucket,remote_key,local_path,size,status,error)
		VALUES(?,?,?,?,?,?,?,?)
		ON CONFLICT(message_id,remote_key) DO UPDATE SET local_path=excluded.local_path, size=excluded.size,
		status=excluded.status, error=excluded.error, pulled_at=CURRENT_TIMESTAMP`,
		r.MessageID, r.Provider, r.Bucket, r.RemoteKey, r.LocalPath, r.Size, r.Status, r.Error)
	return err
}

func (d *DB) DoneKeys(messageID int64) (map[string]string, error) {
	rows, err := d.Query(`SELECT remote_key, local_path FROM pull_records WHERE message_id=? AND status='done'`, messageID)
	if err != nil {
		return nil, err
	}
	defer rows.Close()
	out := map[string]string{}
	for rows.Next() {
		var k, p string
		rows.Scan(&k, &p)
		out[k] = p
	}
	return out, rows.Err()
}

func nilStr(s string) any {
	if s == "" {
		return nil
	}
	return s
}

func nilTime(t time.Time) any {
	if t.IsZero() {
		return nil
	}
	return t
}

var _ = fmt.Sprint // keep fmt imported for future use
