#!/usr/bin/env python
# Copyright 2021-2025 Seth Johnson
# See the top-level LICENSE file for details.
# SPDX-License-Identifier: Apache-2.0
from datetime import timedelta, datetime
from collections import defaultdict, namedtuple
from pathlib import Path
import json
import os
import os.path
import shutil
import sqlite3
try:
    from tqdm import tqdm
except ImportError:
    tqdm = lambda x: x


APPLE_EPOCH = datetime(2001, 1, 1)
NATIVE_EPOCH = datetime(1970, 1, 1)
EPOCH_DELTA = APPLE_EPOCH - NATIVE_EPOCH
NS_TO_SECONDS = 1000000000

def apple_to_dt(created_date):
    return APPLE_EPOCH + timedelta(seconds=created_date)


def dt_to_apple(dt):
    return (dt - APPLE_EPOCH).total_seconds()


def trim_filename(filename):
    if filename.startswith('~/'):
        filename = filename[2:]
    elif filename.startswith('/var/mobile/'):
        filename = filename[12:]
    return filename


class ConnectedDatabase:
    def __init__(self, filename):
        if not filename.exists():
            raise RuntimeError(f"No database file exists at {filename}")

        self.connection = sqlite3.connect(f"file:{filename!s}?mode=ro", uri=True)
        self.cursor = self.connection.cursor()
        self._col_names = {}

    def col_names(self, table):
        try:
            return self._col_names[table]
        except KeyError:
            pass
        cmd = f"PRAGMA table_info({table})"
        result = [r[1] for r in self.cursor.execute(cmd)]
        self._col_names[table] = result
        return result

    def nice_row(self, table, colname, value):
        # Question-mark selection won't substitute into table/colname
        rows = list(self.cursor.execute(
            f'SELECT * FROM {table} WHERE {colname} = {value}'))
        if len(rows) != 1:
            raise TypeError(f"Expected 1 row but got {rows}")
        return dict(zip(self.col_names(table), rows[0]))

    def execute(self, *args):
        return self.cursor.execute(*args)


Chat = namedtuple('Chat', ['id', 'guid', 'identifier'])
Message = namedtuple('Message', ['id', 'date', 'who', 'value'])
Attachment = namedtuple('Attachment', ['path', 'uti', 'name', 'date'])


class Manifest(ConnectedDatabase):
    def __init__(self, root):
        self.root = Path(root)
        super().__init__(self.root / "manifest.db")

    def get_path(self, filename):
        fileids = list(self.cursor.execute(
            "select fileID from Files where relativePath == ?",
            (str(filename),)))
        if len(fileids) > 1:
            raise TypeError(f"Unexpected number of results: got {fileids}")
        elif not fileids:
            raise FileNotFoundError(f"Missing file from manifest: {filename}")
        fid = fileids[0][0]
        return self.root / fid[:2] / fid


class Messages(ConnectedDatabase):
    def __init__(self, manifest, min_date=None, max_date=None):
        """Initialize from a manifest object to load the SMS database.
        """
        filename = manifest.get_path('Library/SMS/sms.db')
        if not filename.exists():
            raise RuntimeError(f"Expected SMS database at {filename}")
        super().__init__(filename)

        # Save a list of handles (sender IDs)
        self._handles = dict(self.execute("SELECT ROWID, id FROM handle"))

        # Retain the method for getting filenames
        self.get_backup_filename = manifest.get_path

        if min_date is not None:
            min_date = dt_to_apple(min_date) * NS_TO_SECONDS
        if max_date is not None:
            max_date = dt_to_apple(max_date) * NS_TO_SECONDS

        date_restrict = ""
        if min_date and max_date:
            date_restrict = f"AND m.date BETWEEN {min_date} AND {max_date}"
        elif min_date:
            date_restrict = f"AND m.date >= {min_date}"
        elif max_date:
            date_restrict = f"AND m.date < {max_date}"

        self.date_restrict = date_restrict


    def get_enumerated_chat_names(self):
        """Return chat ID and phone numbers for each chat (may be duplicates).
        """
        rows = self.execute("SELECT ROWID, guid, chat_identifier FROM chat")
        for r in rows:
            yield Chat(*r)

    def get_attachments(self, chat_id):
        """Return all IDs, handles, date stamps, and text from a given chat ID.
        """
        rows = self.execute(
            "SELECT maj.message_id, "
            "a.filename, a.uti, a.transfer_name, a.created_date "
            "FROM attachment a "
            "LEFT JOIN message_attachment_join maj "
            "ON a.ROWID = maj.attachment_id "
            "LEFT JOIN chat_message_join cmj "
            "ON maj.message_id = cmj.message_id "
            "WHERE cmj.chat_id = ?", (chat_id,)
        )
        attachments = defaultdict(list)
        for (mid, path, uti, name, date) in rows:
            if path is None:
                print(f"Missing filename for attachment {mid}")
                continue
            path = trim_filename(path)
            if name is None:
                name = os.path.basename(path)
            attachments[mid].append(Attachment(path, uti, name, date))
        return attachments

    def get_messages(self, chat_id):
        """Return all IDs, handles, date stamps, and text from a given chat ID.
        """
        attachments = self.get_attachments(chat_id)

        rows = self.execute(
            "SELECT ROWID, date, handle_id, account, is_from_me, "
            "text, cache_has_attachments "
            "FROM message m "
            "LEFT JOIN chat_message_join cmj "
            "ON m.ROWID = cmj.message_id "
            "WHERE cmj.chat_id = ? "
            + self.date_restrict,
            (chat_id,)
        )

        for (rowid, date, handle_id, account, is_from_me, content,
                has_attachments) in rows:
            date = apple_to_dt(date // NS_TO_SECONDS) # convert from ns
            if is_from_me:
                if account:
                    sender = account.partition(':')[-1] or account
                else:
                    sender = None
            elif handle_id == 0:
                sender = "<unknown>"
            else:
                try:
                    sender = self._handles[handle_id]
                except KeyError:
                    print(f"Failed handle id {handle_id} in message {rowid}")
                    print(self.nice_row('message', 'ROWID', rowid))
                    sender = handle_id
            if has_attachments:
                content = attachments[rowid] + [content]
            yield Message(rowid, date, sender, content)

    def copy_attachment(self, msg_id, att, att_root):
        new_name = f'{msg_id}-{att.name}'
        dst = att_root / new_name
        try:
            shutil.copy(self.get_backup_filename(att.path), dst)
        except FileNotFoundError as e:
            print(f"Failed to copy attachment {att} in message {msg_id}:", e)
            return None

        ctime = (EPOCH_DELTA + timedelta(seconds=att.date)).total_seconds()
        os.utime(dst, (ctime, ctime))
        return new_name

    def export_chat(self, chat, root_path):
        dst_path = root_path / f'{chat.identifier}'
        att_root = None
        result = []
        for msg in self.get_messages(chat.id):
            msg = msg._asdict()
            msg['date'] = msg['date'].strftime('%Y%b%d %H:%M:%S').upper()
            val = msg['value']
            if isinstance(val, list):
                # Copy and convert attachments
                newval = []
                for att in val:
                    if not isinstance(att, Attachment):
                        # Text payload
                        newval.append(att)
                        continue
                    if att_root is None:
                        # Lazy creation of attachments dir
                        att_root = dst_path / 'attachments'
                        att_root.mkdir(parents=True, exist_ok=True)
                    new_name = self.copy_attachment(msg['id'], att, att_root)
                    newval.append({'name': new_name,
                                   'uti': att.uti,
                                   'orig': att.path})
                msg['value'] = newval
            result.append(msg)
        if not result:
            # Old recipient with no new chats
            # print(f"No messages found for chat {chat.identifier}")
            return
        guid = chat.guid.replace(';', '')
        dst_path.mkdir(exist_ok=True)
        with open(dst_path / f'messages-{guid}.json', 'w') as f:
            json.dump(result, f, indent=1)

def main():
    """Load a manifest from your backup directory (should look like
    file:`~/Library/Application Support/MobileSync/Backup/{uid}/`) and export
    to the given destination directory
    """
    import argparse
    from datetime import datetime

    parser = argparse.ArgumentParser(description='Export iPhone messages from a backup.')
    parser.add_argument('source', help='Source backup directory')
    parser.add_argument('destination', help='Destination directory for exported messages')
    parser.add_argument('--min-date', help='Only export messages after this date (YYYY-MM-DD)')
    parser.add_argument('--max-date', help='Only export messages before this date (YYYY-MM-DD)')
    args = parser.parse_args()

    # Parse date arguments if provided
    min_date = None
    max_date = None
    if args.min_date:
        min_date = datetime.strptime(args.min_date, '%Y-%m-%d')
    if args.max_date:
        max_date = datetime.strptime(args.max_date, '%Y-%m-%d')

    destination = Path(args.destination)
    destination.mkdir(exist_ok=True)
    
    manifest = Manifest(Path(args.source))
    msg = Messages(manifest, min_date=min_date, max_date=max_date)
    chats = list(msg.get_enumerated_chat_names())
    for c in tqdm(chats):
        msg.export_chat(c, destination)

if __name__ == '__main__':
    main()
