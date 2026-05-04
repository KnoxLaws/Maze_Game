"""
MAZE:RACE — A maze game with three modes:
  1. Solo       — race the clock through one maze
  2. PvP        — two players race side-by-side on identical mazes
                  (P1 = WASD, P2 = Arrow Keys)
  3. Player vs AI — race a BFS-pathfinding AI on identical mazes side-by-side
                    (begins with a 5-second countdown)

Tested on Python 3.14 / VS Code integrated terminal (Windows + macOS/Linux).
Run with:  python3 maze_race.py
"""

# ===========================================================================
# SETUP — Tools the program needs to do its job
# ---------------------------------------------------------------------------
# Before a chef cooks, they get out their pots, pans, and knives. Before a
# Python program runs, it "imports" the toolkits it needs. Each line below
# grabs a different toolkit:
#   - os     : lets us clear the screen and check if we're on Windows or Mac
#   - sys    : lets us read keyboard input the moment a key is pressed
#   - time   : lets us measure how long the race takes and pause briefly
#   - random : lets us scramble the maze layout differently each game
#   - deque  : a fast list-like tool used by the AI to plan its route
#
# Macs and Windows handle keyboards differently behind the scenes, so we
# grab a DIFFERENT keyboard toolkit depending on which one is running.
# ===========================================================================

import os
import sys
import time
import random
from collections import deque

# ---------------------------------------------------------------------------
# Cross-platform non-blocking keyboard input.
# Windows uses msvcrt; macOS/Linux uses termios + select.
# The "# type: ignore" tells VS Code's Pylance not to warn about msvcrt on Mac
# (it's a Windows-only module that we only ever import when running on Windows).
# ---------------------------------------------------------------------------
_IS_WINDOWS = os.name == 'nt'

if _IS_WINDOWS:
    import msvcrt  # type: ignore
else:
    import select
    import termios
    import tty


# ===========================================================================
# KEYBOARD INPUT — Reading WASD and arrow keys without freezing the game
# ---------------------------------------------------------------------------
# Normally when Python asks the keyboard for input, the program FREEZES and
# waits until you press Enter. That's a problem for a live race — we can't
# have the game stop every time we want to check if someone pressed a key.
#
# This section solves that. It puts the keyboard into a special mode where
# we can ask, "is anyone pressing anything RIGHT NOW?" — and instantly find
# out without waiting. If a key is pressed, we grab it. If not, we move on.
#
# It also handles a sneaky problem: arrow keys aren't really one key from
# the computer's point of view. They actually arrive as a SEQUENCE of 2 or
# 3 invisible characters in a row. This section recognizes those sequences
# and turns them into the words "UP", "DOWN", "LEFT", and "RIGHT" so
# Player 2 can use the arrow keys.
# ===========================================================================


class InputHandler:
    """Context manager that puts the terminal into 'raw' mode so we can
    read single keypresses without waiting for Enter.

    Maintains a buffer of pending key events across frames, AND tracks
    partial escape-sequence state so arrow keys split across frames
    don't get misread as separate characters.
    """

    def __init__(self):
        self.old_settings = None
        self.buffer = []        # queue of decoded key events
        self.escape_state = 0   # 0=normal, 1=got ESC, 2=got ESC+'[' (Unix only)

    def __enter__(self):
        if not _IS_WINDOWS:
            # Save terminal settings so we can restore them on exit
            self.old_settings = termios.tcgetattr(sys.stdin)
            tty.setcbreak(sys.stdin.fileno())
        return self

    def __exit__(self, *args):
        if not _IS_WINDOWS and self.old_settings:
            # Always restore the terminal to its original state
            termios.tcsetattr(sys.stdin, termios.TCSADRAIN, self.old_settings)

    def get_key(self):
        """Return the next pending key event, or None if there are none.
        Arrow keys come back as 'UP', 'DOWN', 'LEFT', 'RIGHT'.
        Regular keys come back as lowercase letters ('w', 'a', etc.)."""
        self._drain()
        if self.buffer:
            return self.buffer.pop(0)
        return None

    def drain_keys(self):
        """Discard any pending keystrokes — used during the countdown so
        keys mashed before the race starts don't trigger movement."""
        self._drain()
        self.buffer.clear()
        self.escape_state = 0

    def _drain(self):
        """Read all currently-available bytes and decode them into key
        events stored in self.buffer. Tracks partial escape sequences
        across calls so arrow keys aren't lost between frames."""
        if _IS_WINDOWS:
            self._drain_windows()
        else:
            self._drain_unix()

    def _drain_windows(self):
        # On Windows, arrow keys arrive as a 2-byte sequence: 0xe0 then a code
        while msvcrt.kbhit():  # type: ignore[name-defined]
            ch = msvcrt.getch()  # type: ignore[name-defined]
            if self.escape_state == 0:
                if ch in (b'\xe0', b'\x00'):
                    # Start of an arrow-key sequence — wait for second byte
                    self.escape_state = 1
                else:
                    try:
                        key = ch.decode('utf-8', errors='ignore').lower()
                        if key:
                            self.buffer.append(key)
                    except Exception:
                        pass
            elif self.escape_state == 1:
                arrow = {b'H': 'UP', b'P': 'DOWN',
                         b'K': 'LEFT', b'M': 'RIGHT'}.get(ch)
                if arrow:
                    self.buffer.append(arrow)
                self.escape_state = 0

    def _drain_unix(self):
        # On Mac/Linux, arrow keys arrive as a 3-byte sequence: ESC [ A/B/C/D
        while select.select([sys.stdin], [], [], 0)[0]:
            ch = sys.stdin.read(1)
            if self.escape_state == 0:
                if ch == '\x1b':
                    self.escape_state = 1   # got the ESC, wait for '['
                else:
                    self.buffer.append(ch.lower())
            elif self.escape_state == 1:
                if ch == '[':
                    self.escape_state = 2   # got '[', wait for letter
                else:
                    # Wasn't an arrow sequence — drop it and resume normally
                    self.escape_state = 0
            elif self.escape_state == 2:
                arrow = {'A': 'UP', 'B': 'DOWN',
                         'C': 'RIGHT', 'D': 'LEFT'}.get(ch)
                if arrow:
                    self.buffer.append(arrow)
                self.escape_state = 0


# ===========================================================================
# MAZE CREATION — Building a random maze from scratch every game
# ---------------------------------------------------------------------------
# This section is the maze itself: the walls, the open paths, the start
# corner, and the green goal corner.
#
# Building the maze: we start with a grid that is solid wall everywhere
# (every spot is a '#'). Then we use a technique called "recursive
# backtracking". Imagine a tiny digger that starts in one corner, randomly
# picks a direction, knocks down walls as it carves out a path, and keeps
# going until it hits a dead end. When it gets stuck, it backtracks and
# tries different directions. When it finishes, every empty spot in the
# maze can be reached from every other empty spot, with exactly ONE route
# between any two points (no shortcuts, no loops).
#
# This section also includes the AI's "brain": a search routine called
# Breadth-First Search (BFS). Imagine pouring water into the start cell
# and watching it spread one step at a time, filling every reachable cell.
# The moment the water touches the goal, we look back at the path the water
# took to get there — that's the shortest possible route, and it becomes
# the AI's plan.
# ===========================================================================
class MazeGame:
    """Generates and stores a single maze layout that one or two players race on."""

    # ANSI color codes — VS Code's integrated terminal supports these by default
    COLOR_RESET = "\033[0m"
    COLOR_WALL  = "\033[97m"   # bright white  — walls
    COLOR_PATH  = "\033[90m"   # dark grey     — open path
    COLOR_P1    = "\033[94m"   # bright blue   — Player 1 / human
    COLOR_P2    = "\033[93m"   # bright yellow — Player 2 / AI
    COLOR_GOAL  = "\033[92m"   # bright green  — finish line
    COLOR_TITLE = "\033[96m"   # cyan          — UI titles

    def __init__(self, width=15, height=15):
        # Maze generation requires odd dimensions so walls fall on even indexes
        self.width  = width  if width  % 2 != 0 else width  + 1
        self.height = height if height % 2 != 0 else height + 1

        # Build one maze that BOTH players will use — identical layouts
        self.maze = self._generate_random_maze(self.width, self.height)

        # Start at top-left, goal at bottom-right
        self.start_pos = [1, 1]
        self.goal_pos  = [self.height - 2, self.width - 2]
        self.maze[self.goal_pos[0]][self.goal_pos[1]] = 'G'

    def _generate_random_maze(self, w, h):
        """Recursive backtracker: carves a perfect maze (one path between any
        two open cells, no loops)."""
        grid = [['#' for _ in range(w)] for _ in range(h)]

        def carve(r, c):
            grid[r][c] = '.'
            # Move two cells at a time so we keep wall cells between corridors
            directions = [(0, 2), (0, -2), (2, 0), (-2, 0)]
            random.shuffle(directions)
            for dr, dc in directions:
                nr, nc = r + dr, c + dc
                if 0 < nr < h - 1 and 0 < nc < w - 1 and grid[nr][nc] == '#':
                    # Knock down the wall between current and next cell
                    grid[r + dr // 2][c + dc // 2] = '.'
                    carve(nr, nc)

        carve(1, 1)
        return grid

    def find_shortest_path(self, start, goal):
        """Breadth-first search from start to goal. Returns a list of moves
        ('w','a','s','d'). Used by the AI player."""
        queue = deque([(tuple(start), [])])
        visited = {tuple(start)}
        # Each move tuple: (row delta, col delta, key character)
        moves = [(-1, 0, 'w'), (1, 0, 's'), (0, -1, 'a'), (0, 1, 'd')]

        while queue:
            (r, c), path = queue.popleft()
            if [r, c] == goal:
                return path
            for dr, dc, key in moves:
                nr, nc = r + dr, c + dc
                if (0 <= nr < self.height and 0 <= nc < self.width
                        and self.maze[nr][nc] != '#'
                        and (nr, nc) not in visited):
                    visited.add((nr, nc))
                    queue.append(((nr, nc), path + [key]))
        return []  # No path found (shouldn't happen with a perfect maze)


# ===========================================================================
# PLAYERS — The racers themselves (humans and the AI)
# ---------------------------------------------------------------------------
# A "Player" is just a bundle of information: a name, a color, and where
# they currently are in the maze. They also know how to MOVE. When given
# a direction (W, A, S, or D for up/left/down/right), they check whether
# there's a wall in the way and either step forward or stay put.
#
# An "AIPlayer" is a special kind of Player that controls itself. It uses
# the BFS search from above to plan the entire route to the goal in
# advance, then walks that route one step at a time. The "difficulty"
# setting controls how long the AI waits between each step:
#   - Easy   = waits longer between moves (slow walker, easy to outrace)
#   - Medium = a moderate pace
#   - Hard   = barely waits at all (very fast — good luck)
# ===========================================================================
class Player:
    """A human-controlled player. Tracks position and color."""

    def __init__(self, name, color):
        self.name = name
        self.color = color
        self.position = [1, 1]   # everyone starts at top-left

    def move(self, direction, game):
        """Try to move in 'w'/'a'/'s'/'d'. Returns True if the move happened."""
        new_pos = self.position.copy()
        if   direction == 'w': new_pos[0] -= 1
        elif direction == 's': new_pos[0] += 1
        elif direction == 'a': new_pos[1] -= 1
        elif direction == 'd': new_pos[1] += 1
        else: return False

        # Stay inside the grid AND don't walk through walls
        if 0 <= new_pos[0] < game.height and 0 <= new_pos[1] < game.width:
            if game.maze[new_pos[0]][new_pos[1]] != '#':
                self.position = new_pos
                return True
        return False


class AIPlayer(Player):
    """An AI player. Uses BFS to find the optimal path, then walks it.
    Difficulty controls how often the AI is allowed to move (its 'speed')."""

    def __init__(self, name, color, difficulty='medium'):
        super().__init__(name, color)
        # Lower delay = the AI moves more often = harder to beat
        delay_by_difficulty = {'easy': 0.45, 'medium': 0.25, 'hard': 0.12}
        self.move_delay = delay_by_difficulty.get(difficulty, 0.25)
        self.last_move_time = 0.0
        self.path = []  # cached BFS path

    def take_turn(self, game):
        """Move once if enough time has passed since the AI's last move."""
        now = time.time()
        if now - self.last_move_time < self.move_delay:
            return False  # still 'thinking' — keeps the race fair

        # Compute the path lazily, only the first time
        if not self.path:
            self.path = game.find_shortest_path(self.position, game.goal_pos)

        if self.path:
            next_move = self.path.pop(0)
            self.move(next_move, game)
            self.last_move_time = now
            return True
        return False


# ===========================================================================
# DRAWING THE SCREEN — Painting the maze in the terminal
# ---------------------------------------------------------------------------
# The terminal is just a black box that prints letters and numbers. To
# create the illusion of a moving game, we erase the entire screen about
# 25 times per second and re-draw EVERYTHING from scratch — walls, paths,
# players, timer, and all. Done fast enough, our eyes see it as smooth
# animation, like a flipbook where each page is slightly different.
#
# When two people are racing side-by-side, we draw TWO copies of the
# maze, with each player visible only on their OWN board. We line up the
# player labels above each maze and put a wide gap between them so neither
# player gets distracted by their opponent's progress.
#
# The countdown function uses the same drawing routine, but instead of
# showing "Time left", it shows "Race starts in 5..." down to 1, then
# "GO!" — and the actual race timer only starts after the countdown ends.
# ===========================================================================
def clear_screen():
    os.system('cls' if os.name == 'nt' else 'clear')


def render(games, players, time_left, status):
    """Draw the game state. games[i] is the maze that players[i] plays on.
    For solo or 'same maze' modes, games can contain the same MazeGame twice."""
    clear_screen()

    print()
    print(f"  {MazeGame.COLOR_TITLE}══════ MAZE:RACE ══════{MazeGame.COLOR_RESET}")
    print(f"  Time left: {time_left}s   |   {status}")
    print()

    # All mazes must share the same dimensions (we generate them that way)
    height = games[0].height
    width  = games[0].width

    # --- Player name headers, lined up over each maze ---
    header = "  "
    for idx, p in enumerate(players):
        # Each maze cell prints as 2 chars ("# ", ". ", etc.)
        maze_visual_width = width * 2
        # Pad name to fill the maze's width
        label = f"{p.color}{p.name}{MazeGame.COLOR_RESET}"
        # Account for invisible color codes when padding
        visible_len = len(p.name)
        padding = max(0, maze_visual_width - visible_len)
        header += label + " " * padding
        if idx < len(players) - 1:
            header += " " * 48  # wide gap between mazes (48 spaces)
    print(header)

    # --- Render each row, side-by-side for each player ---
    for r in range(height):
        line = "  "
        for idx, player in enumerate(players):
            game = games[idx]   # this player's own maze
            for c in range(width):
                if [r, c] == player.position:
                    # Show this player on their own board
                    line += f"{player.color}P {MazeGame.COLOR_RESET}"
                elif game.maze[r][c] == '#':
                    line += f"{MazeGame.COLOR_WALL}# {MazeGame.COLOR_RESET}"
                elif game.maze[r][c] == 'G':
                    line += f"{MazeGame.COLOR_GOAL}G {MazeGame.COLOR_RESET}"
                else:
                    line += f"{MazeGame.COLOR_PATH}. {MazeGame.COLOR_RESET}"
            if idx < len(players) - 1:
                # Wide gap between mazes with a subtle vertical divider
                # (24 spaces + divider + 23 spaces = 48 visible chars,
                # matching the header gap width above)
                line += f"{' ' * 24}{MazeGame.COLOR_PATH}║{MazeGame.COLOR_RESET}{' ' * 23}"
        print(line)
    print()

    # --- Controls reminder at the bottom ---
    if len(players) == 1:
        print("  Controls: WASD to move  |  Q to quit")
    elif len(players) == 2 and isinstance(players[1], AIPlayer):
        print(f"  You: {players[0].color}WASD{MazeGame.COLOR_RESET}  |  AI moves automatically  |  Q to quit")
    else:
        print(f"  P1: {players[0].color}WASD{MazeGame.COLOR_RESET}   "
              f"P2: {players[1].color}Arrow Keys{MazeGame.COLOR_RESET}   |  Q to quit")


def run_countdown(games, players, seconds, ih):
    """Show a visual countdown before a race starts.
    Drains any keys mashed during the countdown so they don't move players."""
    for n in range(seconds, 0, -1):
        ih.drain_keys()  # ignore impatient mashing
        render(games, players, 0,
               f"{MazeGame.COLOR_GOAL}Race starts in {n}...{MazeGame.COLOR_RESET}")
        time.sleep(1)
    ih.drain_keys()
    render(games, players, 0,
           f"{MazeGame.COLOR_GOAL}🏁 GO! 🏁{MazeGame.COLOR_RESET}")
    time.sleep(0.5)
    ih.drain_keys()


# ===========================================================================
# THE GAME MODES — The three different ways to play
# ---------------------------------------------------------------------------
# Each of the three game modes follows the same basic recipe:
#   1. Build the maze (or two of them, for PvP "different mazes" mode).
#   2. Create the player(s) — a human, two humans, or a human and an AI.
#   3. Run the main loop. Over and over, very quickly:
#        a. Draw the current state of the screen.
#        b. Check if anyone has won or if the timer hit zero — if so, stop.
#        c. See what keys have been pressed since the last frame.
#        d. Move the players in response.
#        e. Pause for 1/25th of a second so the game runs at a steady speed.
#
# What's different between the three modes:
#   - SOLO mode: one player, one maze, race the clock (60-second limit).
#   - PvP mode: two humans race side-by-side. They can play either the
#               SAME maze (S) or two DIFFERENT mazes (D). First to reach
#               the green G wins. 90-second limit.
#   - Vs AI mode: a human and an AI race the same maze. Begins with a
#                 5-second countdown so you have time to brace yourself
#                 before the AI starts walking. 90-second limit.
# ===========================================================================
def play_solo(time_limit=60):
    """One player races the clock through a single maze."""
    game = MazeGame()
    p1 = Player("PLAYER", MazeGame.COLOR_P1)
    start_time = time.time()
    status = "Find the green G!"

    with InputHandler() as ih:
        while True:
            elapsed = time.time() - start_time
            time_left = max(0, int(time_limit - elapsed))
            render([game], [p1], time_left, status)

            # Win condition
            if p1.position == game.goal_pos:
                print(f"\n  {MazeGame.COLOR_GOAL}🏁 VICTORY! Finished in {elapsed:.1f}s{MazeGame.COLOR_RESET}\n")
                break
            # Loss condition
            if time_left == 0:
                print(f"\n  ⏰ TIME'S UP!\n")
                break

            # Process all keys queued this frame (only one move per frame though)
            quit_pressed = False
            moved = False
            while True:
                key = ih.get_key()
                if key is None:
                    break
                if key == 'q':
                    quit_pressed = True
                    break
                elif key in ('w', 'a', 's', 'd') and not moved:
                    p1.move(key, game)
                    moved = True
                    status = "Exploring..."
            if quit_pressed:
                break

            # Tiny sleep so we don't spin the CPU at 100%
            time.sleep(0.04)


def play_vs_player(time_limit=90, different_mazes=False):
    """Two humans race on side-by-side mazes.
       P1 uses WASD, P2 uses the arrow keys.
       If different_mazes is True, each player gets their OWN unique maze.
       If False, both players race on the exact same layout."""
    # Generate Player 1's maze. Player 2 either shares it (same-maze mode)
    # or gets their own freshly-generated maze (different-mazes mode).
    game1 = MazeGame()
    game2 = MazeGame() if different_mazes else game1

    p1 = Player("PLAYER 1", MazeGame.COLOR_P1)
    p2 = Player("PLAYER 2", MazeGame.COLOR_P2)
    if different_mazes:
        status = "Different mazes! First to G wins!"
    else:
        status = "Same maze! First to G wins!"

    # Player 2 uses the arrow keys — get_key() returns these strings for arrows
    p2_keymap = {'UP': 'w', 'DOWN': 's', 'LEFT': 'a', 'RIGHT': 'd'}

    with InputHandler() as ih:
        start_time = time.time()
        while True:
            elapsed = time.time() - start_time
            time_left = max(0, int(time_limit - elapsed))
            render([game1, game2], [p1, p2], time_left, status)

            # Each player checks the goal of THEIR OWN maze
            p1_won = p1.position == game1.goal_pos
            p2_won = p2.position == game2.goal_pos
            if p1_won or p2_won:
                winner = p1 if p1_won else p2
                print(f"\n  {winner.color}🏁 {winner.name} WINS! ({elapsed:.1f}s){MazeGame.COLOR_RESET}\n")
                break
            if time_left == 0:
                print(f"\n  ⏰ TIME'S UP! It's a draw.\n")
                break

            # Process every key queued this frame so BOTH players can move
            # in the same frame (one move each, max). Eliminates "lost moves"
            # when both players press a key in the same 40ms window.
            p1_moved = False
            p2_moved = False
            quit_pressed = False
            while True:
                key = ih.get_key()
                if key is None:
                    break
                if key == 'q':
                    quit_pressed = True
                    break
                elif key in ('w', 'a', 's', 'd') and not p1_moved:
                    # Each player moves on their own maze (these are the same
                    # object in same-maze mode, different objects otherwise)
                    p1.move(key, game1)
                    p1_moved = True
                elif key in p2_keymap and not p2_moved:
                    p2.move(p2_keymap[key], game2)
                    p2_moved = True
            if quit_pressed:
                break

            time.sleep(0.04)


def play_vs_ai(difficulty='medium', time_limit=90):
    """Player races an AI on identical side-by-side mazes.
       Begins with a 5-second countdown."""
    game = MazeGame()
    p1 = Player("PLAYER", MazeGame.COLOR_P1)
    ai = AIPlayer(f"AI ({difficulty})", MazeGame.COLOR_P2, difficulty)
    status = "Beat the AI to the goal!"

    with InputHandler() as ih:
        # 5-second countdown before the race begins
        # AI shares the same maze as the player (no different-maze option here)
        run_countdown([game, game], [p1, ai], 5, ih)

        # Now start the actual race timer
        start_time = time.time()
        # Reset the AI's clock too so it doesn't move five times the instant we begin
        ai.last_move_time = time.time()

        while True:
            elapsed = time.time() - start_time
            time_left = max(0, int(time_limit - elapsed))
            render([game, game], [p1, ai], time_left, status)

            # Check for a winner
            p1_won = p1.position == game.goal_pos
            ai_won = ai.position == game.goal_pos
            if p1_won or ai_won:
                winner = p1 if p1_won else ai
                print(f"\n  {winner.color}🏁 {winner.name} WINS! ({elapsed:.1f}s){MazeGame.COLOR_RESET}\n")
                break
            if time_left == 0:
                print(f"\n  ⏰ TIME'S UP!\n")
                break

            # Process all queued keys (one player move per frame max)
            quit_pressed = False
            moved = False
            while True:
                key = ih.get_key()
                if key is None:
                    break
                if key == 'q':
                    quit_pressed = True
                    break
                elif key in ('w', 'a', 's', 'd') and not moved:
                    p1.move(key, game)
                    moved = True
            if quit_pressed:
                break

            # AI moves on its own clock (governed by difficulty delay)
            ai.take_turn(game)

            time.sleep(0.04)


# ===========================================================================
# THE MENU — What you see when the program first starts up
# ---------------------------------------------------------------------------
# This is the title screen. It shows the game name, lists the three modes,
# and waits for the player to type a number to pick one. After each game
# ends, the player can press R to play that same mode again with a fresh
# maze, or just press Enter to come back to this menu.
# ===========================================================================
def main_menu():
    while True:
        clear_screen()
        print(f"\n  {MazeGame.COLOR_TITLE}╔══════════════════════════╗{MazeGame.COLOR_RESET}")
        print(f"  {MazeGame.COLOR_TITLE}║     --- MAZE:RACE ---    ║{MazeGame.COLOR_RESET}")
        print(f"  {MazeGame.COLOR_TITLE}╚══════════════════════════╝{MazeGame.COLOR_RESET}\n")
        print("  1. Solo (race the clock)")
        print("  2. Player vs Player")
        print("  3. Player vs AI")
        print("  Q. Quit\n")

        choice = input("  Choose mode: ").strip().lower()

        if choice == '1':
            # Loop replays Solo with a brand-new maze if the player presses R
            while True:
                play_solo()
                again = input("\n  Press R to play again, or Enter for menu: ").strip().lower()
                if again != 'r':
                    break
        elif choice == '2':
            # Ask whether the two players race on the same maze or different ones
            print("\n  Maze choice:  S = Same maze   D = Different mazes")
            m = input("  Choose: ").strip().lower()
            different_mazes = (m == 'd')
            # Loop replays Player vs Player with brand-new mazes if R is pressed
            # (the same/different choice is preserved across replays)
            while True:
                play_vs_player(different_mazes=different_mazes)
                again = input("\n  Press R to play again, or Enter for menu: ").strip().lower()
                if again != 'r':
                    break
        elif choice == '3':
            print("\n  Difficulty:  1 = Easy   2 = Medium   3 = Hard")
            d = input("  Choose difficulty: ").strip()
            difficulty = {'1': 'easy', '2': 'medium', '3': 'hard'}.get(d, 'medium')
            # Loop replays Player vs AI at the SAME difficulty if R is pressed
            while True:
                play_vs_ai(difficulty)
                again = input("\n  Press R to play again, or Enter for menu: ").strip().lower()
                if again != 'r':
                    break
        elif choice == 'q':
            print("\n  Thanks for playing!\n")
            break


# ===========================================================================
# PROGRAM START — The "ON button" for the whole game
# ---------------------------------------------------------------------------
# This block at the very bottom is what actually runs first when you type
# "python3 maze_race.py" and hit Enter. It opens the menu, and if the
# player force-quits by pressing Ctrl+C, it exits politely with a "Bye!"
# message instead of dumping a scary red error message all over the screen.
# ===========================================================================
if __name__ == "__main__":
    try:
        main_menu()
    except KeyboardInterrupt:
        # Ctrl+C should exit cleanly without a scary traceback
        print("\n\n  Interrupted. Bye!\n")
