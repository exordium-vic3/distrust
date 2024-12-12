import os
import random
import asyncio
import discord
from discord.ext import commands
from discord.ui import View, Button
from dotenv import load_dotenv

load_dotenv()

# ---------------- Configuration ----------------
INTENTS = discord.Intents.default()
INTENTS.messages = True
INTENTS.message_content = True
bot = commands.Bot(command_prefix="!", intents=INTENTS)

# Game states stored in memory. In production, consider a more robust store.
# game_id -> { "players": [player1_id, player2_id],
#              "roles": {player_id: "Crewmate" or "Impostor"},
#              "channel_id": channel_id,
#              "active": True/False,
#              "start_time": timestamp,
#              "message_id": The ID of the message with buttons }
active_games = {}

HELP_TEXT = """**DISTRUST Game Instructions:**

- **Starting a Game:**  
  Mention me (the bot) and another user to start a new game. Example: `@Bot @OtherUser`

- **Roles:**  
  Each player will be secretly assigned one of the following roles:
  - Crewmate
  - Impostor

  Possible distributions:
  1. Two Crewmates
  2. Two Impostors
  3. One Crewmate and One Impostor

- **Your Goal:**
  - If you are a Crewmate:
    - Press **Trust** if you think the other player is also a Crewmate.
    - Press **Distrust** if you think the other player is an Impostor.
  - If you are an Impostor:
    - If facing a Crewmate, your goal is to trick them into pressing **Trust**, 
      or press **Trust** if you predict they'll Distrust.
    - If facing another Impostor, the first to press **Trust** or **Distrust** wins immediately.

- **Win Conditions:**
  - **Two Impostors:** The first to press either button wins.
  - **One Crewmate & One Impostor:** 
    - If **Distrust** is pressed by anyone, the Crewmate wins.
    - If the Crewmate presses **Trust**, the Impostor wins.
    - If the Impostor presses **Trust**, the Crewmate wins.
  - **Two Crewmates:**
    - If either player presses **Trust**, both players win together.
    - If either player presses **Distrust**, the other player (who did not press Distrust) wins.
  - If no one presses a button within 5 minutes:
    - Two Impostors or Two Crewmates: both lose.
    - One Impostor, One Crewmate: the Impostor wins by default.

- **The game ends immediately after the first button press.** 
"""

# Helper function to determine game results
def determine_winner(roles, presser_id, button_pressed, other_id):
    # roles is a dict: {player_id: "Crewmate" or "Impostor"}
    p1_id, p2_id = list(roles.keys())
    p1_role = roles[p1_id]
    p2_role = roles[p2_id]

    presser_role = roles[presser_id]
    other_role = roles[other_id]

    # Case 1: Two Impostors
    if p1_role == "Impostor" and p2_role == "Impostor":
        # First to press wins
        return f"<@{presser_id}> pressed **{button_pressed}** and wins! Both were Impostors."

    # Case 2: One Crewmate, One Impostor
    if {p1_role, p2_role} == {"Crewmate", "Impostor"}:
        if button_pressed == "Distrust":
            # Distrust pressed: Crewmate wins
            if presser_role == "Crewmate":
                return f"<@{presser_id}> (Crewmate) pressed **Distrust**. Crewmate wins!"
            else:
                # Impostor pressed Distrust also leads to a Crewmate win.
                # The other player is the Crewmate who didn't press yet:
                return f"<@{presser_id}> (Impostor) pressed **Distrust**. The Crewmate wins!"
        else:  # Trust was pressed
            # If Crewmate presses Trust -> Impostor wins
            # If Impostor presses Trust -> Crewmate wins
            if presser_role == "Crewmate":
                return f"<@{presser_id}> (Crewmate) pressed **Trust**. The Impostor wins!"
            else:
                return f"<@{presser_id}> (Impostor) pressed **Trust**. The Crewmate wins!"

    # Case 3: Two Crewmates
    if p1_role == "Crewmate" and p2_role == "Crewmate":
        if button_pressed == "Trust":
            # Either pressing Trust means both players win
            return f"<@{p1_id}> and <@{p2_id}> are both Crewmates. **Trust** was pressed, both win!"
        else: # Distrust was pressed
            # The one who did NOT press Distrust wins
            return f"<@{presser_id}> pressed **Distrust**, causing them to lose. <@{other_id}> wins!"

    # Should not reach here logically
    return "Error determining winner."

async def end_game_no_buttons(game_id, channel):
    game = active_games.get(game_id)
    if not game or not game["active"]:
        return
    # Determine what happens if no one presses a button in 5 mins.
    roles = game["roles"]
    pids = list(roles.keys())
    p1, p2 = pids[0], pids[1]
    r1, r2 = roles[p1], roles[p2]
    # If one Impostor and one Crewmate: Impostor wins by default
    if {r1, r2} == {"Crewmate", "Impostor"}:
        # Find who is the Impostor:
        for pid, role in roles.items():
            if role == "Impostor":
                await channel.send(f"No one pressed a button in time! The Impostor <@{pid}> wins by default.")
                break
    else:
        # Two Impostors or Two Crewmates: both lose
        await channel.send("No one pressed a button in time! Both lose.")
    game["active"] = False

class DistrustView(View):
    def __init__(self, game_id):
        super().__init__(timeout=None)
        self.game_id = game_id

    @discord.ui.button(label="Trust", style=discord.ButtonStyle.success)
    async def trust_button(self, interaction: discord.Interaction, button: Button):
        await self.handle_press(interaction, "Trust")

    @discord.ui.button(label="Distrust", style=discord.ButtonStyle.danger)
    async def distrust_button(self, interaction: discord.Interaction, button: Button):
        await self.handle_press(interaction, "Distrust")

    async def handle_press(self, interaction: discord.Interaction, button_pressed: str):
        game = active_games.get(self.game_id)
        if not game or not game["active"]:
            await interaction.response.send_message("This game is no longer active.", ephemeral=True)
            return

        # First button press decides the outcome
        presser_id = interaction.user.id
        if presser_id not in game["players"]:
            await interaction.response.send_message("You are not a player in this game.", ephemeral=True)
            return

        # Determine the other player
        other_id = [p for p in game["players"] if p != presser_id][0]

        result = determine_winner(game["roles"], presser_id, button_pressed, other_id)
        game["active"] = False
        # Disable buttons after game ends
        for child in self.children:
            child.disabled = True

        await interaction.response.edit_message(content=result, view=self)
        # End the game officially, no further presses allowed.

@bot.event
async def on_ready():
    print(f"Logged in as {bot.user}")

@bot.event
async def on_message(message):
    if message.author.bot:
        return

    # If the bot is mentioned:
    if bot.user.mentioned_in(message):
        # Check for help request
        if "help" in message.content.lower():
            await message.channel.send(HELP_TEXT)
            return

        # Try to find another user mention to start the game
        mentions = [m for m in message.mentions if m.id != bot.user.id]
        if len(mentions) == 1:
            player1 = message.author
            player2 = mentions[0]

            # Check if these players are already in a game
            for g in active_games.values():
                if g["active"] and (player1.id in g["players"] or player2.id in g["players"]):
                    await message.channel.send("One of the players is already in an active game.")
                    return

            # Start a new game
            roles_combo = random.choice([
                ("Crewmate", "Crewmate"),
                ("Impostor", "Impostor"),
                ("Crewmate", "Impostor"),
                ("Impostor", "Crewmate")
            ])
            
            # Assign roles randomly to each player
            p1_role, p2_role = roles_combo
            roles = {
                player1.id: p1_role,
                player2.id: p2_role
            }

            # DM each player their role
            try:
                await player1.send(f"Your role is: **{p1_role}**")
                await player2.send(f"Your role is: **{p2_role}**")
            except discord.Forbidden:
                await message.channel.send("I cannot start the game because I cannot DM one of the players.")
                return

            # Create the game object
            game_id = f"{player1.id}-{player2.id}-{message.channel.id}-{message.id}"
            active_games[game_id] = {
                "players": [player1.id, player2.id],
                "roles": roles,
                "channel_id": message.channel.id,
                "active": True,
                "start_time": discord.utils.utcnow()
            }

            # Create the buttons
            view = DistrustView(game_id)
            msg = await message.channel.send(f"DISTRUST game started between <@{player1.id}> and <@{player2.id}>. Press Trust or Distrust:", view=view)
            active_games[game_id]["message_id"] = msg.id

            # Start a timer to end the game after 5 minutes if no presses
            async def end_game_later():
                await asyncio.sleep(300)  # 5 minutes
                # If still active and no button pressed, end
                if active_games.get(game_id, {}).get("active"):
                    ch = bot.get_channel(active_games[game_id]["channel_id"])
                    await end_game_no_buttons(game_id, ch)

            bot.loop.create_task(end_game_later())
        else:
            # Mentioned bot but no valid second user -> no game start
            await message.channel.send("Please mention exactly one other user to start a game, or use 'help' for instructions.")


bot.run(os.getenv("DISCORD_TOKEN"))
