-- Reactions are annotations on their target message, not detached crawler
-- events.  This migration deliberately deletes the previous audit stream.
ALTER TABLE messages
    ADD COLUMN IF NOT EXISTS reaction_annotation_json JSONB NOT NULL DEFAULT '[]'::jsonb;

DROP TABLE IF EXISTS crawled_message_reactions;
