-- 010_agent_messages.sql
-- Agent-to-agent messaging table

CREATE TABLE IF NOT EXISTS agent_messages (
    id TEXT PRIMARY KEY,
    sender_id TEXT NOT NULL,
    recipient_id TEXT NOT NULL,
    message_type TEXT NOT NULL DEFAULT 'text',
    payload_json TEXT NOT NULL DEFAULT '{}',
    status TEXT NOT NULL DEFAULT 'pending',
    created_at TEXT NOT NULL,
    read_at TEXT,
    FOREIGN KEY(sender_id) REFERENCES agent_nodes(id),
    FOREIGN KEY(recipient_id) REFERENCES agent_nodes(id)
);

CREATE INDEX IF NOT EXISTS idx_agent_messages_recipient ON agent_messages(recipient_id, status);
CREATE INDEX IF NOT EXISTS idx_agent_messages_sender ON agent_messages(sender_id, created_at);
