#!/usr/bin/env python3
"""
╔══════════════════════════════════════╗
║           MAZE  RACE  v2.0           ║
╠══════════════════════════════════════╣
║  Mode 1 : Player  vs  AI             ║
║           Same maze, real-time race  ║
║  Mode 2 : Player  vs  Player         ║
║           Two mazes, side by side    ║
╠══════════════════════════════════════╣
║  Controls                            ║
║   Player 1 / Solo  :  W A S D        ║
║   Player 2 (PvP)   :  I J K L        ║
║   Quit             :  Q              ║
╚══════════════════════════════════════╝

Traps
  O  Portal    – teleports you to a random far-away cell
  X  Mega Trap – teleports you to the cell farthest from the goal
"""

import os
import sys
import random
import time
import threading
import collections
import queue as _Q


# ─────────────────────────────────────────────────────────────────────────────
# ANSI colour constants
# ─────────────────────────────────────────────────────────────────────────────
RST  = "\033[0m"
WALL = "\033[97m"   # bright white
PATH = "\033[90m"   # dark grey
P1   = "\033[94m"   # blue     – player 1 / solo player
AIc  = "\033[93m"   # yellow   – AI opponent
P2   = "\033[96m"   # cyan     – player 2
GOAL = "\033[92m"   # green    – goal cell
PO   = "\033[91m"   # red      – portal  O
PX   = "\033[95m"   # magenta  – mega-trap X
BOLD = "\033[1m"


# ─────────────────────────────────────────────────────────────────────────────
# Maze generation  (recursive back-tracker / randomised DFS)
# ─────────────────────────────────────────────────────────────────────────────
def build_maze(w: int, h: int) -> list[list[str]]:
    """
    Generate a perfect maze (no loops, fully connected) using
    randomised depth-first search / recursive back-tracker.

    Both dimensions are forced to odd numbers so that walls and
    open cells alternate on a 2-step grid.
    """
    w |= 1
    h |= 1
    grid = [['#'] * w for _ in range(h)]

    def carve(r: int, c: int) -> None:
        """Open cell (r, c) and carve passages to unvisited neighbours."""
        grid[r][c] = '.'
        directions = [(0, 2), (0, -2), (2, 0), (-2, 0)]
        random.shuffle(directions)
        for dr, dc in directions:
            nr, nc = r + dr, c + dc
            if 0 < nr < h - 1 and 0 < nc < w - 1 and grid[nr][nc] == '#':
                # knock down the wall between current cell and neighbour
                grid[r + dr // 2][c + dc // 2] = '.'
                carve(nr, nc)

    # Raise recursion limit so large mazes do not blow the stack
    sys.setrecursionlimit(max(sys.getrecursionlimit(), w * h))
    carve(1, 1)
    return grid


def place_traps(grid: list[list[str]], h: int, w: int,
                reserved: set, n_portals: int = 3) -> None:
    """
    Scatter portals (O) and one mega-trap (X) onto random open cells,
    avoiding positions in the reserved set (start, goal).
    """
    def drop(symbol: str, count: int) -> None:
        placed = 0
        while placed < count:
            r = random.randint(1, h - 2)
            c = random.randint(1, w - 2)
            if grid[r][c] == '.' and (r, c) not in reserved:
                grid[r][c] = symbol
                reserved.add((r, c))
                placed += 1

    drop('O', n_portals)
    drop('X', 1)


# ─────────────────────────────────────────────────────────────────────────────
# BFS path-finder  (used exclusively by the AI)
# ─────────────────────────────────────────────────────────────────────────────
def bfs_path(
    grid:  list[list[str]],
    start: list[int],
    goal:  list[int],
    avoid: set | None = None,
) -> list[list[int]]:
    """
    Breadth-first search from start to goal.
    Returns the full path as a list of [row, col] steps (including start),
    or an empty list if no path exists.

    Cells whose (row, col) tuple is in `avoid` are treated as walls.
    """
    avoid = avoid or set()
    h, w  = len(grid), len(grid[0])
    src, dst = tuple(start), tuple(goal)

    queue = collections.deque([(src, [src])])
    seen  = {src}

    while queue:
        pos, path = queue.popleft()
        if pos == dst:
            return [list(p) for p in path]
        for dr, dc in ((-1, 0), (1, 0), (0, -1), (0, 1)):
            nxt = (pos[0] + dr, pos[1] + dc)
            if (
                0 <= nxt[0] < h
                and 0 <= nxt[1] < w
                and grid[nxt[0]][nxt[1]] != '#'
                and nxt not in seen
                and nxt not in avoid
            ):
                seen.add(nxt)
                queue.append((nxt, path + [nxt]))
    return []


# ─────────────────────────────────────────────────────────────────────────────
# Non-blocking keyboard input  (one daemon thread, cross-platform)
# ─────────────────────────────────────────────────────────────────────────────
_keyq: _Q.Queue[str] = _Q.Queue()


def _kb_daemon() -> None:
    """
    Continuously read single keypresses (no Enter required) into _keyq.
    Uses msvcrt on Windows and termios/tty on Unix.
    This function runs forever as a daemon thread.
    """
    if os.name == 'nt':
        # ── Windows ──────────────────────────────────────────────────────────
        import msvcrt
        while True:
            if msvcrt.kbhit():
                ch = msvcrt.getwch()
                _keyq.put((ch.decode() if isinstance(ch, bytes) else ch).lower())
            time.sleep(0.02)
    else:
        # ── Unix / macOS ──────────────────────────────────────────────────────
        import tty
        import termios
        import select

        fd  = sys.stdin.fileno()
        old = termios.tcgetattr(fd)   # save original terminal settings
        tty.setraw(fd)                # enter raw mode (single-char reads, no echo)
        try:
            while True:
                # Non-blocking check: wait up to 20 ms for a character
                if select.select([sys.stdin], [], [], 0.02)[0]:
                    _keyq.put(sys.stdin.read(1).lower())
        finally:
            # Restore terminal on unexpected exit
            termios.tcsetattr(fd, termios.TCSADRAIN, old)


def _start_kb() -> None:
    """Launch the keyboard daemon thread (call once at startup)."""
    t = threading.Thread(target=_kb_daemon, daemon=True)
    t.start()


def _poll() -> str | None:
    """Return one queued keypress, or None if the queue is empty."""
    try:
        return _keyq.get_nowait()
    except _Q.Empty:
        return None


def _wait_key() -> str:
    """Block until at least one key is available, then return it."""
    while True:
        k = _poll()
        if k is not None:
            return k
        time.sleep(0.02)


# ─────────────────────────────────────────────────────────────────────────────
# MazeState  –  one player's view of one maze
# ─────────────────────────────────────────────────────────────────────────────
class MazeState:
    """
    Stores the maze grid, the player's position, and all movement logic
    for a single participant (human or AI).

    Pass shared_grid to give two participants the SAME physical grid
    (Player vs AI mode).  Omit it to generate a fresh maze (PvP mode).
    """

    def __init__(
        self,
        w: int = 13,
        h: int = 13,
        shared_grid: list[list[str]] | None = None,
    ) -> None:

        if shared_grid is not None:
            # Share an already-built grid (PvAI: player and AI use the same maze)
            self.grid = shared_grid
            self.h    = len(shared_grid)
            self.w    = len(shared_grid[0])
        else:
            # Build an independent maze (PvP: each player gets their own)
            self.w    = w | 1
            self.h    = h | 1
            self.grid = build_maze(self.w, self.h)
            reserved  = {(1, 1), (self.h - 2, self.w - 2)}
            self.grid[self.h - 2][self.w - 2] = 'G'
            place_traps(self.grid, self.h, self.w, reserved)

        self.goal = [self.h - 2, self.w - 2]
        self.pos  = [1, 1]
        self.done = False

    # ── movement ──────────────────────────────────────────────────────────────
    def try_move(self, dr: int, dc: int) -> str:
        """
        Attempt to step (dr, dc) from the current position.

        Returns one of:
          'wall'    – blocked by a wall or border
          'move'    – normal open-path step
          'portal'  – stepped on O: teleported to a random far cell
          'mega'    – stepped on X: teleported to the farthest cell
          'goal'    – reached the exit G
        """
        nr, nc = self.pos[0] + dr, self.pos[1] + dc

        # Boundary check
        if not (0 <= nr < self.h and 0 <= nc < self.w):
            return 'wall'

        cell = self.grid[nr][nc]
        if cell == '#':
            return 'wall'

        # Commit the move
        self.pos = [nr, nc]

        if cell == 'G':
            self.done = True
            return 'goal'
        if cell == 'O':
            self._teleport_random()
            return 'portal'
        if cell == 'X':
            self._teleport_farthest()
            return 'mega'
        return 'move'

    # ── teleport helpers ──────────────────────────────────────────────────────
    def _teleport_random(self) -> None:
        """
        Warp to a random open cell that is at least 5 Manhattan steps
        from the goal (so portals always set you back meaningfully).
        """
        for _ in range(2000):
            r = random.randint(1, self.h - 2)
            c = random.randint(1, self.w - 2)
            if (
                self.grid[r][c] == '.'
                and abs(r - self.goal[0]) + abs(c - self.goal[1]) > 5
            ):
                self.pos = [r, c]
                return
        self.pos = [1, 1]   # fallback if maze is tiny

    def _teleport_farthest(self) -> None:
        """
        Warp to whichever open cell has the greatest Manhattan distance
        from the goal – the harshest possible setback.
        """
        best, best_dist = [1, 1], -1
        for r in range(self.h):
            for c in range(self.w):
                if self.grid[r][c] == '.':
                    d = abs(r - self.goal[0]) + abs(c - self.goal[1])
                    if d > best_dist:
                        best_dist, best = d, [r, c]
        self.pos = best


# ─────────────────────────────────────────────────────────────────────────────
# Renderer  –  converts a MazeState into a list of coloured row-strings
# ─────────────────────────────────────────────────────────────────────────────
def render(
    state:       MazeState,
    p_color:     str,
    p_sym:       str,
    extra_pos:   list[int] | None = None,
    extra_color: str | None = None,
    extra_sym:   str | None = None,
) -> list[str]:
    """
    Render the maze grid to a list of ANSI-coloured strings (one per row).

    p_color / p_sym   – appearance of state.pos (the human player).
    extra_*           – optional second entity on the same grid (the AI).
    """
    rows = []
    for ri, row in enumerate(state.grid):
        line = ""
        for ci, cell in enumerate(row):
            here = [ri, ci]
            if here == state.pos:
                line += f"{p_color}{p_sym}{RST} "
            elif extra_pos is not None and here == extra_pos:
                line += f"{extra_color}{extra_sym}{RST} "
            elif cell == '#':
                line += f"{WALL}#{RST} "
            elif cell == 'G':
                line += f"{GOAL}G{RST} "
            elif cell == 'O':
                line += f"{PO}O{RST} "
            elif cell == 'X':
                line += f"{PX}X{RST} "
            else:
                line += f"{PATH}.{RST} "
        rows.append(line)
    return rows


# ─────────────────────────────────────────────────────────────────────────────
# MODE 1  –  Player  vs  AI  (same maze, simultaneous real-time race)
# ─────────────────────────────────────────────────────────────────────────────
class PvAI:
    """
    One maze shared by the human player (P) and an AI opponent (A).
    Both start at [1,1] and race to reach G.

    AI strategy
    ───────────
    • BFS finds the shortest path to the goal.
    • X (mega-trap) cells are excluded from BFS so the AI never steps on them.
    • If the AI walks into a portal (O) it is teleported, then re-runs BFS.
    • The AI takes one step every AI_SPEED seconds – tune this for difficulty.
    """

    AI_SPEED   = 0.65   # seconds between AI moves  (lower = harder)
    TIME_LIMIT = 60     # seconds before the round expires

    def __init__(self) -> None:
        # ── Build one shared maze ─────────────────────────────────────────────
        W, H = 13, 13
        shared = build_maze(W, H)
        shared[H - 2][W - 2] = 'G'
        reserved = {(1, 1), (H - 2, W - 2)}
        place_traps(shared, H, W, reserved)

        # Player and AI share the grid but track their own positions
        self.player  = MazeState(shared_grid=shared)
        self.ai_pos  = [1, 1]
        self.ai_path : list[list[int]] = []
        self._recalc_ai()

        self.winner : str | None = None
        self._lock  = threading.Lock()   # protects shared state

    # ── AI helpers ────────────────────────────────────────────────────────────
    def _recalc_ai(self) -> None:
        """
        Recompute BFS from the AI's current position to the goal,
        steering clear of X (mega-trap) cells.
        """
        avoid = {
            (r, c)
            for r in range(self.player.h)
            for c in range(self.player.w)
            if self.player.grid[r][c] == 'X'
        }
        path = bfs_path(self.player.grid, self.ai_pos, self.player.goal, avoid)
        # path[0] == current position; skip it so ai_path holds only future steps
        self.ai_path = path[1:] if path else []

    def _ai_teleport_random(self) -> None:
        """Teleport the AI to a random open cell far from the goal."""
        h, w, goal = self.player.h, self.player.w, self.player.goal
        for _ in range(2000):
            r = random.randint(1, h - 2)
            c = random.randint(1, w - 2)
            if (
                self.player.grid[r][c] == '.'
                and abs(r - goal[0]) + abs(c - goal[1]) > 5
            ):
                self.ai_pos = [r, c]
                return
        self.ai_pos = [1, 1]

    # ── AI background thread ──────────────────────────────────────────────────
    def _ai_worker(self) -> None:
        """
        Background thread: advance the AI one step every AI_SPEED seconds.
        Recalculates its path after a portal teleport.
        Stops when a winner has been determined.
        """
        while True:
            time.sleep(self.AI_SPEED)

            with self._lock:
                if self.winner:
                    return   # game already over

                if not self.ai_path:
                    self._recalc_ai()
                    if not self.ai_path:
                        return   # stuck – won't happen in a perfect maze

                nxt  = self.ai_path.pop(0)
                cell = self.player.grid[nxt[0]][nxt[1]]
                self.ai_pos = nxt

                if cell == 'G':
                    # AI reached the goal first
                    if not self.winner:
                        self.winner = 'ai'
                elif cell == 'O':
                    # Portal: teleport and replan
                    self._ai_teleport_random()
                    self._recalc_ai()
                # X cells are excluded by BFS – the AI never lands on them

    # ── main game loop ────────────────────────────────────────────────────────
    def run(self) -> None:
        """Start the AI thread and drive the display/input loop at ~12 fps."""
        ai_thread = threading.Thread(target=self._ai_worker, daemon=True)
        t0 = time.time()
        ai_thread.start()

        while True:
            elapsed   = time.time() - t0
            time_left = max(0, int(self.TIME_LIMIT - elapsed))

            with self._lock:
                # ── Timeout check ─────────────────────────────────────────────
                if elapsed >= self.TIME_LIMIT and not self.winner:
                    self.winner = 'timeout'

                # ── Process all queued keypresses this frame ──────────────────
                while True:
                    k = _poll()
                    if k is None:
                        break
                    if k in ('q', '\x03', '\x1b'):       # Q / Ctrl-C / Esc
                        self.winner = 'quit'
                        break
                    if   k == 'w': result = self.player.try_move(-1,  0)
                    elif k == 's': result = self.player.try_move( 1,  0)
                    elif k == 'a': result = self.player.try_move( 0, -1)
                    elif k == 'd': result = self.player.try_move( 0,  1)
                    else:          result = None
                    if result == 'goal' and not self.winner:
                        self.winner = 'player'

                # Snapshot shared values before releasing the lock
                win    = self.winner
                ai_pos = self.ai_pos[:]

            # ── Draw frame ────────────────────────────────────────────────────
            rows = render(
                self.player, P1, 'P',
                extra_pos=ai_pos, extra_color=AIc, extra_sym='A',
            )
            os.system('cls' if os.name == 'nt' else 'clear')
            print(f"\n {BOLD}══  PLAYER  vs  AI  ══{RST}")
            print(
                f"  {P1}P{RST} You (WASD)  "
                f"{AIc}A{RST} AI  "
                f"{GOAL}G{RST} Goal  "
                f"{PO}O{RST} Portal  "
                f"{PX}X{RST} MegaTrap  "
                f"Q=quit"
            )
            print(f"  ⏳ {time_left}s remaining\n")
            for line in rows:
                print("  " + line)
            print()

            if win:
                if   win == 'player':  print(f"  {GOAL}{BOLD}🏁  YOU WIN!  Finished in {int(elapsed)}s!{RST}\n")
                elif win == 'ai':      print(f"  {AIc}{BOLD}🤖  AI WINS!  Better luck next time.{RST}\n")
                elif win == 'timeout': print(f"  {PO}{BOLD}⏰  TIME'S UP!  Game over.{RST}\n")
                else:                  print("  Quit.\n")
                break

            time.sleep(0.08)   # ≈ 12 fps refresh


# ─────────────────────────────────────────────────────────────────────────────
# MODE 2  –  Player  vs  Player  (two independent mazes, side by side)
# ─────────────────────────────────────────────────────────────────────────────
class PvP:
    """
    Two separate mazes generated independently, displayed side by side.
    Player 1 (blue)  uses  W A S D.
    Player 2 (cyan)  uses  I J K L.
    First to reach their own G wins.
    """

    TIME_LIMIT = 60

    def __init__(self) -> None:
        self.s1 = MazeState()          # Player 1's private maze
        self.s2 = MazeState()          # Player 2's private maze
        self.winner: str | None = None

    def run(self) -> None:
        """Drive the display/input loop for the PvP race."""
        t0 = time.time()

        # Pre-compute visual maze width once (used for side-by-side alignment)
        maze_vis_w  = self.s1.w * 2    # visible characters per maze row (e.g. 26)
        col_padding = 6                # gap characters between the two mazes
        lbl1_vis    = len("── PLAYER 1 ──")
        lbl_gap     = max(1, maze_vis_w + col_padding - lbl1_vis)

        while True:
            elapsed   = time.time() - t0
            time_left = max(0, int(self.TIME_LIMIT - elapsed))

            if elapsed >= self.TIME_LIMIT and not self.winner:
                self.winner = 'timeout'

            # ── Process all queued keypresses ─────────────────────────────────
            while True:
                k = _poll()
                if k is None:
                    break
                if k in ('q', '\x03', '\x1b'):
                    self.winner = 'quit'
                    break

                # Player 1 – W A S D
                if   k == 'w': r1 = self.s1.try_move(-1,  0)
                elif k == 's': r1 = self.s1.try_move( 1,  0)
                elif k == 'a': r1 = self.s1.try_move( 0, -1)
                elif k == 'd': r1 = self.s1.try_move( 0,  1)
                else:          r1 = None
                if r1 == 'goal' and not self.winner:
                    self.winner = 'p1'

                # Player 2 – I J K L
                if   k == 'i': r2 = self.s2.try_move(-1,  0)
                elif k == 'k': r2 = self.s2.try_move( 1,  0)
                elif k == 'j': r2 = self.s2.try_move( 0, -1)
                elif k == 'l': r2 = self.s2.try_move( 0,  1)
                else:          r2 = None
                if r2 == 'goal' and not self.winner:
                    self.winner = 'p2'

            # ── Render both mazes side by side ────────────────────────────────
            rows1 = render(self.s1, P1, '1')
            rows2 = render(self.s2, P2, '2')
            n_rows = max(len(rows1), len(rows2))
            gap    = " " * col_padding

            os.system('cls' if os.name == 'nt' else 'clear')
            print(f"\n {BOLD}══  PLAYER 1  vs  PLAYER 2  ══{RST}")
            print(
                f"  {P1}1{RST} P1 (WASD)  "
                f"{P2}2{RST} P2 (IJKL)  "
                f"{GOAL}G{RST} Goal  "
                f"{PO}O{RST} Portal  "
                f"{PX}X{RST} MegaTrap  "
                f"Q=quit"
            )
            print(f"  ⏳ {time_left}s remaining\n")

            # Column headers (ANSI codes inflate byte length; use computed gap)
            print(
                f"  {P1}{BOLD}── PLAYER 1 ──{RST}"
                + " " * lbl_gap
                + f"{P2}{BOLD}── PLAYER 2 ──{RST}"
            )

            # Maze rows side by side
            for i in range(n_rows):
                l1 = rows1[i] if i < len(rows1) else ""
                l2 = rows2[i] if i < len(rows2) else ""
                print(f"  {l1}{gap}  {l2}")
            print()

            if self.winner:
                if   self.winner == 'p1':      print(f"  {P1}{BOLD}🏁  PLAYER 1 WINS!  GG!{RST}\n")
                elif self.winner == 'p2':      print(f"  {P2}{BOLD}🏁  PLAYER 2 WINS!  GG!{RST}\n")
                elif self.winner == 'timeout': print(f"  {PO}{BOLD}⏰  TIME'S UP!  Nobody wins.{RST}\n")
                else:                          print("  Quit.\n")
                break

            time.sleep(0.08)   # ≈ 12 fps refresh


# ─────────────────────────────────────────────────────────────────────────────
# Main menu
# ─────────────────────────────────────────────────────────────────────────────
def main() -> None:
    """
    Display the mode-select menu and launch the chosen game.
    The keyboard daemon thread is started once here and lives for the
    entire program.  All UI input (menu + in-game) flows through _keyq.
    """
    _start_kb()

    while True:
        os.system('cls' if os.name == 'nt' else 'clear')
        print(f"\n {BOLD}╔══════════════════════════╗")
        print(f" ║       MAZE  RACE  v2      ║")
        print(f" ╚══════════════════════════╝{RST}\n")
        print(f"   {BOLD}1{RST}  →  Player vs AI")
        print(f"          Same maze. Race the computer in real time.\n")
        print(f"   {BOLD}2{RST}  →  Player vs Player")
        print(f"          Two mazes, side by side. First to finish wins.\n")
        print(f"   {BOLD}Q{RST}  →  Quit\n")
        print(f"  Press a key to choose …")

        k = _wait_key()

        if k == '1':
            PvAI().run()
        elif k == '2':
            PvP().run()
        elif k in ('q', '\x03', '\x1b'):
            os.system('cls' if os.name == 'nt' else 'clear')
            print("\n  See ya!\n")
            sys.exit(0)
        else:
            continue   # unrecognised key – redraw menu

        # After game ends, wait for any key before returning to menu
        print(f"  Press any key to return to the menu …")
        _wait_key()


if __name__ == "__main__":
    main()