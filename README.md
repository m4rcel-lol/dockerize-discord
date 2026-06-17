# Dockerize Bot

Dockerize is a production-style Discord bot that turns a Discord server into a container-like environment. It does **not** run real Docker containers. Instead, each user gets one isolated Discord category with private text and voice channels, managed through slash commands that feel like Docker CLI commands.

## Features

- Slash-command-only Discord bot
- One personal container per user per guild
- Private category with `terminal`, `logs`, `general`, and `runtime`
- Public/private visibility modes
- Invite and uninvite system
- Channel creation/deletion inside containers
- Admin inspection, suspension, force delete, and force visibility controls
- SQLite persistence with `aiosqlite`
- Docker Compose deployment on Alpine Linux
- No message content intent
- No paid APIs or external API keys except the Discord token

## Discord Bot Setup

1. Open the Discord Developer Portal.
2. Create an application.
3. Open **Bot** and create a bot user.
4. Copy the token into `.env` as `DISCORD_TOKEN`.
5. Under **Privileged Gateway Intents**, you do **not** need Message Content Intent.
6. Invite the bot with scopes:
   - `bot`
   - `applications.commands`

Required bot permissions:

- Manage Channels
- Manage Roles
- View Channels
- Send Messages
- Embed Links
- Use External Emojis
- Read Message History
- Connect
- Speak, if voice channels are used

Recommended permissions integer: `3492880`

Invite URL format:

```txt
https://discord.com/oauth2/authorize?client_id=YOUR_CLIENT_ID&permissions=3492880&scope=bot%20applications.commands
```

Make sure the bot role is above roles it needs to manage in Discord role settings.

## Installation

```bash
git clone <your-repo-url> dockerize-bot
cd dockerize-bot
cp .env.example .env
nano .env
```

Set your token:

```env
DISCORD_TOKEN=your-real-bot-token
DATABASE_PATH=/app/data/dockerize.sqlite3
MAX_CHANNELS_PER_CONTAINER=10
DEFAULT_CONTAINER_VISIBILITY=private
ALLOW_BOT_INVITES=false
EMOJI_DOCKER=:docker:
EMOJI_SUCCESS=:success:
EMOJI_FAILURE=:failure:
EMOJI_WARNING=:warning:
SYNC_COMMANDS=true
```

Run it:

```bash
docker compose up -d --build
```

View logs:

```bash
docker compose logs -f dockerize
```

Stop it:

```bash
docker compose down
```

The SQLite database is stored in `./data/dockerize.sqlite3` on your host.

## First Server Setup

In your Discord server, run:

```txt
/setup command_channel:#dockerize staff_role:@Staff
```

Normal users must use Dockerize commands in the configured command channel, or inside their own container when appropriate.

## Commands

### User Container Commands

```txt
/container create name:<name>
/container up
/container down
/container delete confirm:true
/container status
/container public
/container private
/container invite user:@user
/container uninvite user:@user
/container invites
```

### Channel Commands

```txt
/channel create name:<name> type:<text|voice>
/channel delete channel:<channel> confirm:<true|false> force:<true|false>
/channel list
```

### Admin Commands

```txt
/admin container check user:@user reason:<text>
/admin container suspend user:@user reason:<text>
/admin container unsuspend user:@user
/admin container delete user:@user reason:<text>
/admin container list
/admin container info user:@user
/admin container force-private user:@user
/admin container force-public user:@user
```

## Example Flow

1. Admin initializes the daemon:

```txt
/setup command_channel:#dockerize staff_role:@Staff
```

2. User creates a container:

```txt
/container create name:marcel-lab
```

3. User creates a project channel:

```txt
/channel create name:projects type:text
```

4. User invites a friend:

```txt
/container invite user:@friend
```

5. Staff checks a container:

```txt
/admin container check user:@marcel reason:Routine inspection
```

6. User shuts down their container:

```txt
/container down
```

## Custom Emoji Configuration

Custom emojis have different IDs in every server, so Dockerize reads emoji strings from `.env`:

```env
EMOJI_DOCKER=:docker:
EMOJI_SUCCESS=:success:
EMOJI_FAILURE=:failure:
EMOJI_WARNING=:warning:
```

You can also paste full custom emoji markup:

```env
EMOJI_DOCKER=<:docker:123456789012345678>
```

If a custom emoji does not render, the bot still works and shows the raw text.

## Troubleshooting

### Slash commands are not showing

- Wait a few minutes for global command sync.
- Keep `SYNC_COMMANDS=true` in `.env`.
- Reinvite the bot with the `applications.commands` scope.
- Check `docker compose logs -f dockerize`.

### Container creation fails with permission errors

- Give the bot `Manage Channels` and `Manage Roles`.
- Move the bot role above roles it needs to control.
- Make sure it can view the setup command channel.

### DMs do not send

Users may have DMs disabled. Dockerize logs the failure and continues safely.

### SQLite database not persisting

Make sure this volume exists in `docker-compose.yml`:

```yaml
volumes:
  - ./data:/app/data
```

### Alpine build fails while installing dependencies

Rebuild without cache:

```bash
docker compose build --no-cache
```

## Notes

Dockerize is a Discord category/channel manager with a container theme. It never starts real Docker containers, never executes user code, and never shells into the host server.
