# About

This is a hackish tool to export Messages and attachments from an unencrypted
iPhone backup. I've only tested it on my backup (which contains messages that
date back to 2010ish) with
- iOS 14.7 on macOS 11.5
- iOS 15.5 on macOS 11.6

This is meant as a starting point for other people who want to hack around
with their backups and *not* as a finished product of any kind, although it
*may* work the first time for you.

It's my first time using SQL so undoubtedly some of the queries could be run
more efficiently. However, the limiting factor seems to be the bandwidth of
copying files rather than the queries themselves (and/or the `stat`ting which
is less than optimal). But this is supposed to be run maybe once a year at most
when you want to export your messages, rather than repeatedly.

The output is a series of directories named after the chat identifier (usually
the recipient's message handle or `chatNNN...` for group), inside of which is a
JSON messages file. Attachments are copied from the decrypted backup to a
subdirectory inside each chat.

This requires at least Python 3.6, and probably 3.7.

Usage:
```console
$ python3 export.py "~/Library/Application Support/MobileSync/Backup/...."
"~/Desktop/iphone-export"
```
