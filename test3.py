"""
MAZE RACE — A maze game with three modes:
  1. Solo       — race the clock through one maze
  2. PvP        — two players race side-by-side on identical mazes (WASD vs IJKL)
  3. Player vs AI — race a BFS-pathfinding AI on identical mazes side-by-side

Tested on Python 3.14 / VS Code integrated terminal (Windows + macOS/Linux).
Run with:  python maze_race.py
"""

import os
import sys
import time
import random
from collections import deque

# ---------------------------------------------------------------------------
# Cross-platform non-blocking keyboard input.
# Windows uses msvcrt; macOS/Linux uses termios + select.
# This lets both players (or the player + AI) move at the same time.
# ---------------------------------------------------------------------------
if os.name == 'nt':
    import msvcrt
else:
    import select
    import termios
    import tty


class InputHandler:
    """Context manager that puts the terminal into 'raw' mode so we can
    read single keypresses without waiting for Enter."""

    def __init__(self):
        self.is_windows = os.name == 'nt'
        self.old_settings = None

    def __enter__(self):
        if not self.is_windows:
            # Save terminal settings so we can restore them on exit
            self.old_settings = termios.tcgetattr(sys.stdin)
            tty.setcbreak(sys.stdin.fileno())
        return self

    def __exit__(self, *args):
        if not self.is_windows and self.old_settings:
            # Always restore the terminal to its original state
            termios.tcsetattr(sys.stdin, termios.TCSADRAIN, self.old_settings)

    def get_key(self):
        """Return a single pressed key, or None if nothing is pressed."""
        if self.is_windows:
            if msvcrt.kbhit():
                ch = msvcrt.getch()
                try:
                    return ch.decode('utf-8', errors='ignore').lower()
                except Exception:
                    return None
        else:
            # Wait 0 seconds — purely non-blocking poll
            if select.select([sys.stdin], [], [], 0)[0]:
                return sys.stdin.read(1).lower()
        return None


# ---------------------------------------------------------------------------
# Maze generation and the shared maze "board"
# ---------------------------------------------------------------------------
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


# ---------------------------------------------------------------------------
# Player classes
# ---------------------------------------------------------------------------
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


# ---------------------------------------------------------------------------
# Rendering — draws one or two mazes side-by-side
# ---------------------------------------------------------------------------
def clear_screen():
    os.system('cls' if os.name == 'nt' else 'clear')


def render(game, players, time_left, status):
    """Draw the game state. Each player gets their own copy of the maze
    rendered next to the others (so PvP/AI shows TWO mazes side-by-side)."""
    clear_screen()

    print()
    print(f"  {MazeGame.COLOR_TITLE}══════ MAZE RACE ══════{MazeGame.COLOR_RESET}")
    print(f"  Time left: {time_left}s   |   {status}")
    print()

    # --- Player name headers, lined up over each maze ---
    header = "  "
    for idx, p in enumerate(players):
        # Each maze cell prints as 2 chars ("# ", ". ", etc.)
        maze_visual_width = game.width * 2
        # Pad name to fill the maze's width
        label = f"{p.color}{p.name}{MazeGame.COLOR_RESET}"
        # Account for invisible color codes when padding
        visible_len = len(p.name)
        padding = max(0, maze_visual_width - visible_len)
        header += label + " " * padding
        if idx < len(players) - 1:
            header += "     "  # space between mazes
    print(header)

    # --- Render each row, side-by-side for each player ---
    for r in range(game.height):
        line = "  "
        for idx, player in enumerate(players):
            for c in range(game.width):
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
                line += "     "  # gap between the two mazes
        print(line)
    print()

    # --- Controls reminder at the bottom ---
    if len(players) == 1:
        print("  Controls: WASD to move  |  Q to quit")
    elif len(players) == 2 and isinstance(players[1], AIPlayer):
        print(f"  You: {players[0].color}WASD{MazeGame.COLOR_RESET}  |  AI moves automatically  |  Q to quit")
    else:
        print(f"  P1: {players[0].color}WASD{MazeGame.COLOR_RESET}   "
              f"P2: {players[1].color}IJKL{MazeGame.COLOR_RESET}   |  Q to quit")


# ---------------------------------------------------------------------------
# Game modes
# ---------------------------------------------------------------------------
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
            render(game, [p1], time_left, status)

            # Win condition
            if p1.position == game.goal_pos:
                print(f"\n  {MazeGame.COLOR_GOAL}🏁 VICTORY! Finished in {elapsed:.1f}s{MazeGame.COLOR_RESET}\n")
                break
            # Loss condition
            if time_left == 0:
                print(f"\n  ⏰ TIME'S UP!\n")
                break

            key = ih.get_key()
            if key == 'q':
                break
            elif key in ('w', 'a', 's', 'd'):
                p1.move(key, game)
                status = "Exploring..."

            # Tiny sleep so we don't spin the CPU at 100%
            time.sleep(0.04)


def play_vs_player(time_limit=90):
    """Two humans race on identical side-by-side mazes."""
    game = MazeGame()
    p1 = Player("PLAYER 1", MazeGame.COLOR_P1)
    p2 = Player("PLAYER 2", MazeGame.COLOR_P2)
    start_time = time.time()
    status = "First to reach G wins!"

    # Player 2 uses IJKL because arrow keys are awkward in cross-platform terminals
    p2_keymap = {'i': 'w', 'k': 's', 'j': 'a', 'l': 'd'}

    with InputHandler() as ih:
        while True:
            elapsed = time.time() - start_time
            time_left = max(0, int(time_limit - elapsed))
            render(game, [p1, p2], time_left, status)

            # Check for a winner
            p1_won = p1.position == game.goal_pos
            p2_won = p2.position == game.goal_pos
            if p1_won or p2_won:
                winner = p1 if p1_won else p2
                print(f"\n  {winner.color}🏁 {winner.name} WINS! ({elapsed:.1f}s){MazeGame.COLOR_RESET}\n")
                break
            if time_left == 0:
                print(f"\n  ⏰ TIME'S UP! It's a draw.\n")
                break

            # Read whatever key was pressed (could be P1's or P2's)
            key = ih.get_key()
            if key == 'q':
                break
            elif key in ('w', 'a', 's', 'd'):
                p1.move(key, game)
            elif key in p2_keymap:
                p2.move(p2_keymap[key], game)

            time.sleep(0.04)


def play_vs_ai(difficulty='medium', time_limit=90):
    """Player races an AI on identical side-by-side mazes."""
    game = MazeGame()
    p1 = Player("PLAYER", MazeGame.COLOR_P1)
    ai = AIPlayer(f"AI ({difficulty})", MazeGame.COLOR_P2, difficulty)
    start_time = time.time()
    status = "Beat the AI to the goal!"

    with InputHandler() as ih:
        while True:
            elapsed = time.time() - start_time
            time_left = max(0, int(time_limit - elapsed))
            render(game, [p1, ai], time_left, status)

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

            # Player input
            key = ih.get_key()
            if key == 'q':
                break
            elif key in ('w', 'a', 's', 'd'):
                p1.move(key, game)

            # AI moves on its own clock (governed by difficulty delay)
            ai.take_turn(game)

            time.sleep(0.04)


# ---------------------------------------------------------------------------
# Main menu
# ---------------------------------------------------------------------------
def main_menu():
    while True:
        clear_screen()
        print(f"\n  {MazeGame.COLOR_TITLE}╔══════════════════════════╗{MazeGame.COLOR_RESET}")
        print(f"  {MazeGame.COLOR_TITLE}║        MAZE RACE         ║{MazeGame.COLOR_RESET}")
        print(f"  {MazeGame.COLOR_TITLE}╚══════════════════════════╝{MazeGame.COLOR_RESET}\n")
        print("  1. Solo (race the clock)")
        print("  2. Player vs Player")
        print("  3. Player vs AI")
        print("  Q. Quit\n")

        choice = input("  Choose mode: ").strip().lower()

        if choice == '1':
            play_solo()
            input("\n  Press Enter to return to menu...")
        elif choice == '2':
            play_vs_player()
            input("\n  Press Enter to return to menu...")
        elif choice == '3':
            print("\n  Difficulty:  1 = Easy   2 = Medium   3 = Hard")
            d = input("  Choose difficulty: ").strip()
            difficulty = {'1': 'easy', '2': 'medium', '3': 'hard'}.get(d, 'medium')
            play_vs_ai(difficulty)
            input("\n  Press Enter to return to menu...")
        elif choice == 'q':
            print("\n  Thanks for playing!\n")
            break


if __name__ == "__main__":
    try:
        main_menu()
    except KeyboardInterrupt:
        # Ctrl+C should exit cleanly without a scary traceback
        print("\n\n  Interrupted. Bye!\n")
print("hello")