# Telegram Link Footer Bot

A Python Telegram bot that requires both an allowlisted Telegram user ID and a password, publishes authorized users' private posts to one configured channel, removes configured Telegram/website/social-media links, and adds a compact official footer:

```text
📱 Telegram
📸 Instagram
```

The words `Telegram` and `Instagram` are clickable Telegram `text_link` entities. Their destinations are configurable and are not displayed in the message text.

The bot uses the official Telegram Bot API through `python-telegram-bot` and long polling.

## How it works

Telegram sends new channel posts as `channel_post` updates and edits as `edited_channel_post` updates. The channel handlers subscribe to both update types and ignore posts from other channels. Before private content is processed, the bot checks the sender's immutable numeric Telegram user ID against `ALLOWED_USER_IDS` and then requires password authentication; messages from unauthorized or unauthenticated users are never published.

Telegram represents clickable text as a `MessageEntity`:

- `url` entities contain a visible URL.
- `text_link` entities contain arbitrary visible text and store the destination in the entity's `url` field.
- Entity offsets and lengths use UTF-16 code units, not Python string indexes.

The sanitizer handles both raw URL text and these entities. It shifts unaffected formatting entities after removing a link, so bold, italic, underline, code, spoiler, mentions, and other Telegram-supported formatting are preserved when possible. Text and media captions are edited with `editMessageText` and `editMessageCaption` equivalents from the Bot API.

The footer labels use the same native entity mechanism:

- `Telegram` points to `TELEGRAM_FOOTER_URL`.
- `Instagram` points to `INSTAGRAM_FOOTER_URL`.
- Only the labels are rendered; the destination URLs are not shown.

Unrelated external links are retained unless their domain or exact URL is configured for removal.

## Publishing posts

Only users whose numeric Telegram IDs are listed in `ALLOWED_USER_IDS` and who have successfully entered `BOT_PASSWORD` can publish. Send an authenticated user's text post, photo, or other supported media message to the bot in a private chat. A caption is optional for supported media. The bot sanitizes it and publishes the result to `TARGET_CHANNEL_ID`:

- Text posts are sent with their original formatting entities and the clickable footer.
- Media posts are copied with their attachment intact and a sanitized caption.
- The bot replies with a confirmation after the post is published.
- Posts already published in the configured channel are edited in place instead of being copied again.

## Authorization and authentication

Publishing is fail-closed and uses immutable Telegram numeric user IDs, never usernames. `ALLOWED_USER_IDS` must contain one or more comma-separated positive numeric IDs; a missing or invalid value prevents the bot from starting. The allowlist gates private submissions; existing posts in the configured channel continue to be handled by the channel-edit path.

To find a user's numeric ID, use a trusted Telegram ID bot such as `@userinfobot`, or have the user send a test post to this bot and read the `Unauthorized Telegram user id=...` entry in the bot log. Copy only the numeric ID, then add it to `.env`:

```dotenv
ALLOWED_USER_IDS=123456789,987654321
```

Set a strong, unique `BOT_PASSWORD` in `.env`. The first private message from an allowlisted user prompts for the password; the next private text message is checked with a constant-time comparison. Successful authentication persists in the SQLite database configured by `AUTH_DB_PATH`, so restarting the bot does not log users out. The password is never logged, displayed in bot responses, or stored in the database.

After five incorrect password attempts within 15 minutes, the user is temporarily blocked for 15 minutes. Use these private-chat commands:

- `/start` or `/login` — prompt for authentication.
- `/logout` — clear the current authenticated session.
- `/status` — show authentication state, the numeric user ID, successful-post count, and failed-attempt count. It is available only after authentication and does not show the password.

For private submissions, the sender ID and password are checked before link sanitization or any channel publishing call. Unauthorized users receive `⛔ You are not authorized to use this bot.`; incorrect passwords are rejected without publishing.

## Requirements

- Python 3.10+
- A bot token from [@BotFather](https://t.me/BotFather)
- Administrator access to the target channel

## Setup

1. Create a bot with BotFather and copy its token.
2. Copy the environment template:

   ```bash
   cp .env.example .env
   ```

3. Edit `.env`:

   ```dotenv
    BOT_TOKEN=123456789:replace-with-your-bot-token
    BOT_PASSWORD=use_a_strong_random_password
    TARGET_CHANNEL_ID=-1001234567890
    ALLOWED_USER_IDS=123456789,987654321
    AUTH_DB_PATH=auth.sqlite3
    REMOVABLE_DOMAINS=t.me,telegram.me,instagram.com

   REMOVABLE_LINKS=
   TELEGRAM_FOOTER_URL=https://t.me/bekobodbugun
   INSTAGRAM_FOOTER_URL=https://www.instagram.com/bekobodbugun
   LOG_LEVEL=INFO
   ```

4. Create a virtual environment and install dependencies:

   ```bash
   python3 -m venv .venv
   source .venv/bin/activate
   python -m pip install -r requirements.txt
   ```

5. Start the bot:

   ```bash
   python bot.py
   ```

`TARGET_CHANNEL_ID` accepts either a numeric channel ID such as `-1001234567890` or a public channel username such as `@my_channel`. The legacy `CHANNEL_ID` variable is still accepted when `TARGET_CHANNEL_ID` is not set.

## Footer configuration

`TELEGRAM_FOOTER_URL` and `INSTAGRAM_FOOTER_URL` control the destinations of the native clickable footer labels. They default to the official targets shown in `.env.example`, but can be changed without editing the source code.

For example, with the default values the visible footer is:

```text
📱 Telegram
📸 Instagram
```

The displayed `Telegram` label is a `text_link` entity targeting `https://t.me/bekobodbugun`; the displayed `Instagram` label targets `https://www.instagram.com/bekobodbugun`. The URLs are not included in the message text.

## Link removal configuration

Values in `REMOVABLE_DOMAINS` and `REMOVABLE_LINKS` are comma-separated.

- `REMOVABLE_DOMAINS` removes matching domains and their subdomains. Keep this list narrow if unrelated Telegram or Instagram links should remain.
- `REMOVABLE_LINKS` removes exact destinations and takes precedence over domain matching. Entries can use full URLs or scheme-less forms such as `t.me/old_channel`.
- `http://` and `https://` are normalized before matching.
- `t.me/...`, `telegram.me/...`, and Instagram links are recognized when their domain is configured.
- A `text_link` entity is removed even when its visible text does not contain the URL.

For example, to remove only one old Telegram account and one old Instagram account:

```dotenv
REMOVABLE_DOMAINS=
REMOVABLE_LINKS=https://t.me/old_channel,https://www.instagram.com/old_account
```

If a configured footer target already occurs in a post as a raw URL or `text_link` entity, the bot does not add a duplicate footer line. If only one target is present, only the missing label is appended. A legacy raw-URL footer at the end of an edited post is converted to the new clickable-label format.

## Channel permissions

1. Open the target channel's administrators.
2. Add the bot as an administrator.
3. Enable **Post Messages** (`can_post_messages`) to publish private posts.
4. Enable **Edit Messages** (`can_edit_messages`) to update posts already in the channel.
5. Make sure the configured `TARGET_CHANNEL_ID` matches the channel.

A bot cannot edit an arbitrary message in a private chat because the message belongs to the user. Private text and media are therefore copied to the configured channel after sanitization. Channel posts, including images, are edited in place with `editMessageCaption` when they have media.

## Project layout

```text
bot.py          Telegram handlers, authentication, channel filtering, publishing, and edits
auth_store.py   SQLite-backed authentication state and failed-attempt limits
config.py       Environment parsing, channel matching, and user allowlisting
link_utils.py   Selective URL matching, entity handling, and footer logic
test_*.py       Unit tests for configuration and sanitization
.env.example    Safe configuration template
requirements.txt
```

## Development checks

Run the unit tests with:

```bash
python -m unittest -v
```

Optional static checks, if installed:

```bash
ruff check bot.py auth_store.py config.py link_utils.py test_auth_store.py test_bot.py test_config.py test_link_utils.py
mypy bot.py auth_store.py config.py link_utils.py test_auth_store.py test_bot.py test_config.py test_link_utils.py
```

## Troubleshooting

- **No updates:** verify the bot is a channel administrator, has **Post Messages** and **Edit Messages**, and `TARGET_CHANNEL_ID` is correct.
- **`BOT_TOKEN is required`:** copy `.env.example` to `.env` and set the token without committing `.env`.
- **`BOT_PASSWORD is required`:** set a nonblank password in `.env`; do not put it in source code.
- **`ALLOWED_USER_IDS` error:** set at least one comma-separated positive numeric Telegram user ID; the bot fails closed when the list is missing or invalid.
- **Password prompt repeats:** use `/login`, enter the exact `BOT_PASSWORD` in a private text message, and check for failed-attempt blocks in the log. Authentication state is stored in `AUTH_DB_PATH`.
- **Permission errors:** confirm the bot is still an administrator with the required posting/editing rights.
- **Message cannot be published:** verify that `TARGET_CHANNEL_ID` is correct and the bot can post to that channel.
- **Message cannot be edited:** Telegram may reject edits to unsupported content or messages that the bot cannot access; the error is logged and polling continues.

The bot token and password are read only from the environment. The password is never stored in the SQLite database or logs; the database stores only authentication state and counters.

See the official API documentation: https://core.telegram.org/bots/api
