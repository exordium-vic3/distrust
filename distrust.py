import discord
from discord.ext import commands
from discord import app_commands, ui, ButtonStyle
from dotenv import load_dotenv
import random
import asyncio

load_dotenv()

intents = discord.Intents.default()
intents.messages = True
intents.guilds = True
intents.dm_messages = True

bot = commands.Bot(command_prefix="!", intents=intents)

# In-memory storage for ongoing games: keyed by channel_id
# Each entry will have:
# {
#   'players': {player_id: { 'role': 'crewmate'/'impostor', 'user': discord.User }},
#   'started': bool,
#   'finished': bool
# }
games = {}

class TrustDistrustView(ui.View):
    def __init__(self, channel_id):
        super().__init__(timeout=None)
        self.channel_id = channel_id

    @ui.button(label="Trust", style=ButtonStyle.success, custom_id="trust_button")
    async def trust_button(self, interaction: discord.Interaction, button: ui.Button):
        await handle_player_choice(interaction, self.channel_id, "trust")

    @ui.button(label="Distrust", style=ButtonStyle.danger, custom_id="distrust_button")
    async def distrust_button(self, interaction: discord.Interaction, button: ui.Button):
        await handle_player_choice(interaction, self.channel_id, "distrust")

async def handle_player_choice(interaction: discord.Interaction, channel_id: int, choice: str):
    if channel_id not in games:
        await interaction.response.send_message("No active game in this channel.", ephemeral=True)
        return

    game = games[channel_id]

    if game.get('finished', False):
        await interaction.response.send_message("This game is already over.", ephemeral=True)
        return

    # Identify which player pressed the button
    player_id = interaction.user.id
    if player_id not in game['players']:
        await interaction.response.send_message("You are not a participant in this game.", ephemeral=True)
        return

    # Check if a choice has already been made
    if game.get('choice_made', False):
        await interaction.response.send_message("A choice has already been made. The game is over.", ephemeral=True)
        return

    # Mark the choice and determine winner
    result_msg, winner_msg = determine_winner(game, player_id, choice)
    game['finished'] = True
    game['choice_made'] = True

    await interaction.response.send_message(embed=result_msg, ephemeral=False)
    await interaction.followup.send(embed=winner_msg)

def determine_winner(game_data, acting_player_id, action):
    players = list(game_data['players'].items())
    # players is list of tuples: [(player_id, {'role': ...}), (player_id, {'role': ...})]
    p1_id, p1_data = players[0]
    p2_id, p2_data = players[1]

    # Extract roles
    p1_role = p1_data['role']
    p2_role = p2_data['role']

    # Identify acting player (who made the move)
    if p1_id == acting_player_id:
        acting_player_role = p1_role
        other_player_id = p2_id
        other_player_role = p2_role
    else:
        acting_player_role = p2_role
        other_player_id = p1_id
        other_player_role = p1_role

    # ACTION OUTCOMES:

    # If two Impostors:
    #   First to press either button wins.
    if p1_role == "impostor" and p2_role == "impostor":
        # acting player wins
        return build_result_embeds(game_data, acting_player_id, action, winner=acting_player_id, both_win=False)

    # If one Crewmate and one Impostor:
    elif (p1_role == "crewmate" and p2_role == "impostor") or (p1_role == "impostor" and p2_role == "crewmate"):
        if action == "distrust":
            # Distrust always gives crewmate the win in a mixed scenario
            crewmate_id = p1_id if p1_role == "crewmate" else p2_id
            return build_result_embeds(game_data, acting_player_id, action, winner=crewmate_id, both_win=False)
        else:
            # action == "trust"
            if acting_player_role == "crewmate":
                impostor_id = p1_id if p1_role == "impostor" else p2_id
                return build_result_embeds(game_data, acting_player_id, action, winner=impostor_id, both_win=False)
            else:
                # Acting player is impostor and trusts, crewmate wins
                crewmate_id = p1_id if p1_role == "crewmate" else p2_id
                return build_result_embeds(game_data, acting_player_id, action, winner=crewmate_id, both_win=False)

    # If two Crewmates:
    elif p1_role == "crewmate" and p2_role == "crewmate":
        if action == "trust":
            # Both win
            return build_result_embeds(game_data, acting_player_id, action, winner=None, both_win=True)
        else:
            # action == "distrust"
            # The other player (who did not press Distrust) wins
            return build_result_embeds(game_data, acting_player_id, action, winner=other_player_id, both_win=False)

    else:
        # Undefined role combination
        return build_result_embeds(game_data, acting_player_id, action, winner=None, both_win=False)

def build_result_embeds(game_data, acting_player_id, action, winner=None, both_win=False):
    players = list(game_data['players'].items())
    p1_id, p1_data = players[0]
    p2_id, p2_data = players[1]

    # Show final roles and action taken
    description = f"**Roles:**\n<@{p1_id}>: {p1_data['role'].capitalize()}\n<@{p2_id}>: {p2_data['role'].capitalize()}"

    # Action line
    description += f"\n\n**Action Taken:** <@{acting_player_id}> pressed **{action.capitalize()}**."

    result_embed = discord.Embed(title="Game Result", description=description, color=0x00FF00)

    if both_win:
        winner_embed = discord.Embed(title="Winners", description=f"Both <@{p1_id}> and <@{p2_id}> win!", color=0x00FF00)
    elif winner:
        winner_embed = discord.Embed(title="Winner", description=f"<@{winner}> wins!", color=0xFFD700)
    else:
        winner_embed = discord.Embed(title="Result", description="No clear winner.", color=0x808080)

    return result_embed, winner_embed

@bot.event
async def on_ready():
    print(f"Logged in as {bot.user}")
    try:
        synced = await bot.tree.sync()
        print(f"Synced {len(synced)} commands.")
    except Exception as e:
        print(e)

@bot.event
async def on_message(message):
    # Ignore messages from the bot itself
    if message.author == bot.user:
        return

    # Check if someone pings the bot
    if bot.user in message.mentions and not message.mention_everyone:
        # Remove the bot mention from the message content
        content = message.content.replace(f"<@!{bot.user.id}>", "").strip().lower()

        # Check if the message is starting a new game (by mentioning another user)
        mentions = [u for u in message.mentions if u != bot.user]
        if len(mentions) == 1 and "trust" not in content and "distrust" not in content:
            # Start a new game
            await start_game(message.channel, message.author, mentions[0])
            return

        # Otherwise, check if it's a trust/distrust command
        if "trust" in content or "distrust" in content:
            # Find the current game in this channel
            channel_id = message.channel.id
            if channel_id not in games:
                await message.reply("There is no active game in this channel.")
                return

            game = games[channel_id]
            if game.get('finished', False):
                await message.reply("This game is already over.")
                return

            # Identify player
            player_id = message.author.id
            if player_id not in game['players']:
                await message.reply("You are not part of this game.")
                return

            action = "trust" if "trust" in content else "distrust"

            # Check if a choice has already been made
            if game.get('choice_made', False):
                await message.reply("A choice has already been made. The game is over.")
                return

            # Determine winner
            result_msg, winner_msg = determine_winner(game, player_id, action)
            game['finished'] = True
            game['choice_made'] = True

            await message.channel.send(embed=result_msg)
            if both_win := (action == "trust" and all(p['role'] == "crewmate" for p in game['players'].values())):
                await message.channel.send(embed=discord.Embed(title="Winners", description=f"Both <@{list(game['players'].keys())[0]}> and <@{list(game['players'].keys())[1]}> win!", color=0x00FF00))
            elif winner_msg:
                await message.channel.send(embed=winner_msg)

    # Process commands if any
    await bot.process_commands(message)

async def start_game(channel, player1: discord.User, player2: discord.User):
    channel_id = channel.id
    if channel_id in games and not games[channel_id].get('finished', True):
        await channel.send("A game is already in progress here.")
        return

    # Assign roles: Allow all pairings
    role_combinations = [
        ("crewmate", "crewmate"),
        ("impostor", "impostor"),
        ("crewmate", "impostor"),
        ("impostor", "crewmate")
    ]
    p1_role, p2_role = random.choice(role_combinations)

    games[channel_id] = {
        'players': {
            player1.id: {'role': p1_role, 'user': player1},
            player2.id: {'role': p2_role, 'user': player2}
        },
        'started': True,
        'finished': False,
        'choice_made': False
    }

    # Instructions to send via DM
    instructions = (
        "You are playing **DISTRUST**.\n"
        "- If you are a **CREWMATE**: Press **Trust** if you believe the other player is a Crewmate, **Distrust** if you believe they are an Impostor.\n"
        "- If you are an **IMPOSTOR**: Try to get the Crewmate to trust you if they are a Crewmate. If both players are Impostors, the first to press a button wins.\n\n"
        "The game ends immediately when someone presses a button. Good luck!"
    )

    # DM both players their roles and instructions
    try:
        await player1.send(f"**Your role:** {p1_role.capitalize()}\n\n{instructions}")
        await player2.send(f"**Your role:** {p2_role.capitalize()}\n\n{instructions}")
    except discord.Forbidden:
        await channel.send("One of the players has DMs disabled. Please enable DMs and try again.")
        # Clean up the game state
        del games[channel_id]
        return

    # Post buttons in the channel
    view = TrustDistrustView(channel_id)
    await channel.send(
        f"🔔 A game of **DISTRUST** has started between <@{player1.id}> and <@{player2.id}>!\n"
        f"Press a button below or mention me with 'trust' or 'distrust' to make your move.",
        view=view
    )

# Replace "YOUR_BOT_TOKEN" with your actual bot token
bot.run(os.getenv("DISCORD_TOKEN"))
