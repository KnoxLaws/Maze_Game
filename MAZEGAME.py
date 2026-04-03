import os
import random
import time

class MazeGame:
    # ANSI Color Codes
    COLOR_RESET = "\033[0m"
    COLOR_WALL = "\033[97m"    # Bright White
    COLOR_PATH = "\033[90m"    # Dark Grey
    COLOR_PLAYER = "\033[94m"  # Bright Blue
    COLOR_GOAL = "\033[92m"    # Bright Green
    COLOR_TRAP_O = "\033[91m"  # Bright Red (Portals)
    COLOR_TRAP_X = "\033[95m"  # Magenta/dPurple (The Great Reset)

    def __init__(self, width=13, height=13, time_limit=30):
        self.width = width if width % 2 != 0 else width + 1
        self.height = height if height % 2 != 0 else height + 1
        self.time_limit = time_limit
        self.start_time = None
        
        self.maze = self.generate_random_maze(self.width, self.height)
        self.player_pos = [1, 1]
        self.goal_pos = [self.height - 2, self.width - 2]
        self.maze[self.goal_pos[0]][self.goal_pos[1]] = 'G'
        
        # CHANGED: Now placing exactly 3 portals instead of 4
        self.place_traps_o(3) 
        self.place_trap_x()   # 1 Super Trap
        
        self.status_message = "Race to the finish! Watch the clock!"

    def generate_random_maze(self, w, h):
        grid = [['#' for _ in range(w)] for _ in range(h)]
        def walk(r, c):
            grid[r][c] = '.'
            dirs = [(0, 2), (0, -2), (2, 0), (-2, 0)]
            random.shuffle(dirs)
            for dr, dc in dirs:
                nr, nc = r + dr, c + dc
                if 0 < nr < h-1 and 0 < nc < w-1 and grid[nr][nc] == '#':
                    grid[r + dr // 2][c + dc // 2] = '.'
                    walk(nr, nc)
        walk(1, 1)
        return grid

    def place_traps_o(self, count):
        placed = 0
        while placed < count:
            r, c = random.randint(1, self.height - 2), random.randint(1, self.width - 2)
            if self.maze[r][c] == '.' and [r, c] != self.player_pos and [r, c] != self.goal_pos:
                self.maze[r][c] = 'O'
                placed += 1

    def place_trap_x(self):
        while True:
            r, c = random.randint(1, self.height - 2), random.randint(1, self.width - 2)
            if self.maze[r][c] == '.' and [r, c] != self.player_pos and [r, c] != self.goal_pos:
                self.maze[r][c] = 'X'
                break

    def teleport_farthest(self):
        max_dist = -1
        farthest_pos = [1, 1]
        
        for r in range(self.height):
            for c in range(self.width):
                if self.maze[r][c] == '.':
                    dist = abs(r - self.goal_pos[0]) + abs(c - self.goal_pos[1])
                    if dist > max_dist:
                        max_dist = dist
                        farthest_pos = [r, c]
        
        self.player_pos = farthest_pos

    def teleport_random(self):
        while True:
            r, c = random.randint(1, self.height - 2), random.randint(1, self.width - 2)
            if self.maze[r][c] == '.' and abs(r - self.goal_pos[0]) + abs(c - self.goal_pos[1]) > 5:
                self.player_pos = [r, c]
                break

    def display_maze(self):
        os.system('cls' if os.name == 'nt' else 'clear')
        print("\n" * 5)
        
        # Calculate time left
        time_left = self.time_limit
        if self.start_time:
            elapsed = time.time() - self.start_time
            time_left = max(0, int(self.time_limit - elapsed))

        print(f" {self.COLOR_PLAYER}P{self.COLOR_RESET} = Player | "
              f"{self.COLOR_GOAL}G{self.COLOR_RESET} = Goal | "
              f"{self.COLOR_TRAP_O}O{self.COLOR_RESET} = Portal (x3) | "
              f"{self.COLOR_TRAP_X}X{self.COLOR_RESET} = MEGA TRAP")
        print(f" ⏳ TIME LEFT: {time_left}s | STATUS: {self.status_message}\n")

        for r_idx, row in enumerate(self.maze):
            line = " "
            for c_idx, cell in enumerate(row):
                if [r_idx, c_idx] == self.player_pos:
                    line += f"{self.COLOR_PLAYER}P {self.COLOR_RESET}"
                elif cell == '#':
                    line += f"{self.COLOR_WALL}# {self.COLOR_RESET}"
                elif cell == 'G':
                    line += f"{self.COLOR_GOAL}G {self.COLOR_RESET}"
                elif cell == 'O':
                    line += f"{self.COLOR_TRAP_O}O {self.COLOR_RESET}"
                elif cell == 'X':
                    line += f"{self.COLOR_TRAP_X}X {self.COLOR_RESET}"
                else:
                    line += f"{self.COLOR_PATH}. {self.COLOR_RESET}"
            print(line)
        print()

    def move_player(self, direction):
        new_pos = self.player_pos.copy()
        if direction == 'w': new_pos[0] -= 1
        elif direction == 's': new_pos[0] += 1
        elif direction == 'a': new_pos[1] -= 1
        elif direction == 'd': new_pos[1] += 1
        else: return

        if (0 <= new_pos[0] < self.height) and (0 <= new_pos[1] < self.width):
            cell_type = self.maze[new_pos[0]][new_pos[1]]
            
            if cell_type == 'X':
                self.status_message = f"{self.COLOR_TRAP_X}💀 OH NO! Sent to the farthest corner!{self.COLOR_RESET}"
                self.teleport_farthest()
            elif cell_type == 'O':
                self.status_message = f"{self.COLOR_TRAP_O}🌀 Portal jump!{self.COLOR_RESET}"
                self.teleport_random()
            elif cell_type != '#':
                self.player_pos = new_pos
                self.status_message = "Exploring..."
            else:
                self.status_message = "Hit a wall."

    def play(self):
        self.start_time = time.time()  # Start the clock!
        
        while True:
            # Check for timeout before displaying
            elapsed = time.time() - self.start_time
            if elapsed > self.time_limit:
                self.display_maze()
                print(f" {self.COLOR_TRAP_O}⏰ TIME'S UP! You took too long! GAME OVER.{self.COLOR_RESET}")
                break

            self.display_maze()
            
            if self.player_pos == self.goal_pos:
                print(f" {self.COLOR_GOAL}🏁 VICTORY! You escaped in {int(elapsed)} seconds!{self.COLOR_RESET}")
                break
                
            move = input(" Move (WASD): ").strip().lower()
            if move in ['exit', 'quit']: break
            self.move_player(move)

if __name__ == "__main__":
    game = MazeGame(time_limit=30) 
    game.play()

"You proposal for the final project should include:"
"• A description of the new features that will be implemented in the final. -           My final project will be a maze where each" 
"level is not only playable, but will be played simotaniously against the same maze being played by the compter next to" 
"it in real time. The game will include a second maze option that allows two people to play two different mazes in a" 
"person v person race. I will make sure to add comments to previous sections of the code and in the new lines of code."
"• An estimate how long the final will take. -          Final will take approximatley 10 hours to complete." 
"• A plan for what needs to be changed and in what order. -         The first thing I will do is set up the maze to where all games"
"are playable, once done I will set up the second maze option where you can play two mazes at a time with two people "
"in a race, test with a friend, and then creat the code for a robot to play the second maze instead of a person. Clean code"
"and add comments to all sections of the code."
"• A description of what you expect will be the most difficult part will be. -          The most difficult part will either be adding"
"the second maze originally or creating the code for the robot to play the second maze"

