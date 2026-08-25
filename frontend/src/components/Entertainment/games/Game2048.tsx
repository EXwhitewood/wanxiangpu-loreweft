/**
 * 2048 小游戏
 * ===========
 *
 * 经典 4x4 数字合并游戏。使用 React state + Tailwind 渲染网格，
 * 相比贪吃蛇格子少（16 vs 400），不需要 Canvas。
 *
 * 设计要点
 * --------
 * - 4x4 网格，初始随机两格放置 2 或 4
 * - 方向键 / WASD 控制所有格子向该方向滑动
 * - 相同数字相邻则合并为两倍
 * - 每次有效移动后随机生成一个新数字（90% 概率为 2，10% 为 4）
 * - 无法再移动时游戏结束
 * - 达到 2048 视为胜利，可继续游戏刷新高分
 */

import { useEffect, useState, useCallback, useRef } from "react";
import { RotateCcw } from "lucide-react";

// === 游戏常量 ===
const GRID_SIZE = 4;
const STORAGE_KEY = "loreweft:2048_high_score";

type Grid = number[][];  // 4x4 二维数组，0 表示空格

// 各数字对应的 Tailwind 颜色类（pine 色系为主，配 amber/crimson 强调）
const TILE_STYLES: Record<number, string> = {
  0: "bg-pine-100/40",
  2: "bg-pine-50 text-pine-800",
  4: "bg-pine-100 text-pine-800",
  8: "bg-pine-200 text-pine-900",
  16: "bg-pine-300 text-pine-900",
  32: "bg-pine-400 text-white",
  64: "bg-pine-500 text-white",
  128: "bg-magic-400 text-white",
  256: "bg-magic-500 text-white",
  512: "bg-magic-600 text-white",
  1024: "bg-amber-500 text-white",
  2048: "bg-amber-600 text-white",
};

// 字号随数字增大而缩小，避免溢出
const TILE_FONT_SIZE: Record<number, string> = {
  2: "text-2xl",
  4: "text-2xl",
  8: "text-2xl",
  16: "text-xl",
  32: "text-xl",
  64: "text-xl",
  128: "text-lg",
  256: "text-lg",
  512: "text-lg",
  1024: "text-base",
  2048: "text-base",
};

function getTileStyle(value: number): string {
  return TILE_STYLES[value] ?? "bg-crimson-500 text-white";
}

function getTileFontSize(value: number): string {
  if (value === 0) return "";
  return TILE_FONT_SIZE[value] ?? "text-sm";
}

// 创建空网格
function createEmptyGrid(): Grid {
  return Array.from({ length: GRID_SIZE }, () => Array(GRID_SIZE).fill(0));
}

// 在空格中随机放置一个数字（2: 90%, 4: 10%）
function addRandomTile(grid: Grid): Grid {
  const empty: { row: number; col: number }[] = [];
  for (let r = 0; r < GRID_SIZE; r++) {
    for (let c = 0; c < GRID_SIZE; c++) {
      if (grid[r][c] === 0) empty.push({ row: r, col: c });
    }
  }
  if (empty.length === 0) return grid;
  const { row, col } = empty[Math.floor(Math.random() * empty.length)];
  const newGrid = grid.map((row) => [...row]);
  newGrid[row][col] = Math.random() < 0.9 ? 2 : 4;
  return newGrid;
}

// 初始化游戏：空网格 + 两个随机数字
function initGrid(): Grid {
  let grid = createEmptyGrid();
  grid = addRandomTile(grid);
  grid = addRandomTile(grid);
  return grid;
}

// 把一维数组向左滑动+合并，返回 [新数组, 本次合并产生的分数]
function slideAndMergeRow(row: number[]): [number[], number] {
  // 1. 过滤空格
  const filtered = row.filter((v) => v !== 0);
  // 2. 相邻相同则合并（从左到右）
  let scoreGained = 0;
  for (let i = 0; i < filtered.length - 1; i++) {
    if (filtered[i] === filtered[i + 1]) {
      filtered[i] *= 2;
      scoreGained += filtered[i];
      filtered.splice(i + 1, 1);
    }
  }
  // 3. 末尾补 0
  while (filtered.length < GRID_SIZE) filtered.push(0);
  return [filtered, scoreGained];
}

// 矩阵转置（用于上下移动转化为左右移动）
function transpose(grid: Grid): Grid {
  return grid[0].map((_, col) => grid.map((row) => row[col]));
}

// 反转每一行（用于右移、下移）
function reverseRows(grid: Grid): Grid {
  return grid.map((row) => [...row].reverse());
}

// 判断两个网格是否相同（用于检测本次移动是否有效）
function gridsEqual(a: Grid, b: Grid): boolean {
  for (let r = 0; r < GRID_SIZE; r++) {
    for (let c = 0; c < GRID_SIZE; c++) {
      if (a[r][c] !== b[r][c]) return false;
    }
  }
  return true;
}

// 检测是否还有可移动的方向
function canMove(grid: Grid): boolean {
  // 有空格则可移动
  for (let r = 0; r < GRID_SIZE; r++) {
    for (let c = 0; c < GRID_SIZE; c++) {
      if (grid[r][c] === 0) return true;
    }
  }
  // 检查横向相邻是否相同
  for (let r = 0; r < GRID_SIZE; r++) {
    for (let c = 0; c < GRID_SIZE - 1; c++) {
      if (grid[r][c] === grid[r][c + 1]) return true;
    }
  }
  // 检查纵向相邻是否相同
  for (let r = 0; r < GRID_SIZE - 1; r++) {
    for (let c = 0; c < GRID_SIZE; c++) {
      if (grid[r][c] === grid[r + 1][c]) return true;
    }
  }
  return false;
}

// 找出网格中的最大值（用于判断是否达到 2048）
function maxTile(grid: Grid): number {
  let max = 0;
  for (const row of grid) {
    for (const v of row) {
      if (v > max) max = v;
    }
  }
  return max;
}

type Direction = "left" | "right" | "up" | "down";

// 按方向移动网格，返回 [新网格, 本次得分增量, 是否实际发生变化]
function move(grid: Grid, direction: Direction): [Grid, number, boolean] {
  let working = grid.map((row) => [...row]);

  // 通过转置/反转统一转化为"向左移动"
  if (direction === "right") {
    working = reverseRows(working);
  } else if (direction === "up") {
    working = transpose(working);
  } else if (direction === "down") {
    working = transpose(working);
    working = reverseRows(working);
  }

  // 执行向左滑动+合并
  let scoreGained = 0;
  working = working.map((row) => {
    const [newRow, gained] = slideAndMergeRow(row);
    scoreGained += gained;
    return newRow;
  });

  // 还原转置/反转
  if (direction === "right") {
    working = reverseRows(working);
  } else if (direction === "up") {
    working = transpose(working);
  } else if (direction === "down") {
    working = reverseRows(working);
    working = transpose(working);
  }

  const changed = !gridsEqual(grid, working);
  return [working, scoreGained, changed];
}

export default function Game2048() {
  const [grid, setGrid] = useState<Grid>(initGrid);
  const [score, setScore] = useState(0);
  const [highScore, setHighScore] = useState(0);
  const [gameOver, setGameOver] = useState(false);
  const [hasWon, setHasWon] = useState(false);
  const [continueAfterWin, setContinueAfterWin] = useState(false);
  // 防止键盘按住时连续触发移动过快
  const isMovingRef = useRef(false);

  // 初始化最高分
  useEffect(() => {
    const stored = localStorage.getItem(STORAGE_KEY);
    if (stored) setHighScore(parseInt(stored, 10) || 0);
  }, []);

  // 更新最高分
  useEffect(() => {
    if (score > highScore) {
      setHighScore(score);
      localStorage.setItem(STORAGE_KEY, String(score));
    }
  }, [score, highScore]);

  // 执行一次移动
  const doMove = useCallback((direction: Direction) => {
    if (gameOver) return;
    if (isMovingRef.current) return;
    isMovingRef.current = true;

    setGrid((prev) => {
      const [newGrid, gained, changed] = move(prev, direction);
      if (!changed) return prev;

      // 添加新格子
      const withNew = addRandomTile(newGrid);

      // 累加分数
      if (gained > 0) {
        setScore((s) => s + gained);
      }

      // 检查胜利条件
      if (!hasWon && !continueAfterWin && maxTile(withNew) >= 2048) {
        setHasWon(true);
      }

      // 检查游戏结束
      if (!canMove(withNew)) {
        setGameOver(true);
      }

      return withNew;
    });

    // 释放锁（下一帧）
    requestAnimationFrame(() => {
      isMovingRef.current = false;
    });
  }, [gameOver, hasWon, continueAfterWin]);

  // 键盘控制
  useEffect(() => {
    const handleKey = (e: KeyboardEvent) => {
      const key = e.key.toLowerCase();
      let dir: Direction | null = null;
      if (key === "arrowleft" || key === "a") dir = "left";
      else if (key === "arrowright" || key === "d") dir = "right";
      else if (key === "arrowup" || key === "w") dir = "up";
      else if (key === "arrowdown" || key === "s") dir = "down";

      if (dir) {
        e.preventDefault();
        doMove(dir);
      }
    };
    window.addEventListener("keydown", handleKey);
    return () => window.removeEventListener("keydown", handleKey);
  }, [doMove]);

  // 重置游戏
  const resetGame = useCallback(() => {
    setGrid(initGrid());
    setScore(0);
    setGameOver(false);
    setHasWon(false);
    setContinueAfterWin(false);
  }, []);

  // 胜利后继续
  const continueGame = useCallback(() => {
    setContinueAfterWin(true);
  }, []);

  return (
    <div className="flex flex-col items-center gap-3">
      {/* 分数显示 */}
      <div className="flex w-full justify-between text-xs">
        <div className="flex items-center gap-1.5">
          <span className="text-pine-700">当前:</span>
          <span className="font-bold text-magic-700">{score}</span>
        </div>
        <div className="flex items-center gap-1.5">
          <span className="text-pine-700">最高:</span>
          <span className="font-bold text-amber-600">{highScore}</span>
        </div>
      </div>

      {/* 游戏面板 */}
      <div className="relative">
        <div
          className="grid gap-1.5 rounded-lg border border-pine-200 bg-pine-100/40 p-1.5 shadow-sm"
          style={{ gridTemplateColumns: `repeat(${GRID_SIZE}, 1fr)` }}
        >
          {grid.flat().map((value, idx) => (
            <div
              key={idx}
              className={`flex h-14 w-14 items-center justify-center rounded-md font-bold transition-colors duration-150 ${getTileStyle(value)} ${getTileFontSize(value)}`}
            >
              {value !== 0 && value}
            </div>
          ))}
        </div>

        {/* 胜利遮罩 */}
        {hasWon && !continueAfterWin && (
          <div className="absolute inset-0 flex flex-col items-center justify-center rounded-lg bg-white/85 backdrop-blur-sm">
            <p className="text-base font-bold text-amber-600">达成 2048!</p>
            <p className="mt-1 text-xs text-pine-700">当前得分: {score}</p>
            <div className="mt-3 flex gap-2">
              <button
                onClick={continueGame}
                className="rounded-lg bg-magic-600 px-3 py-1.5 text-xs font-medium text-white transition-colors hover:bg-magic-700"
              >
                继续挑战
              </button>
              <button
                onClick={resetGame}
                className="rounded-lg border border-pine-200 bg-white px-3 py-1.5 text-xs font-medium text-pine-700 transition-colors hover:bg-pine-50"
              >
                重开
              </button>
            </div>
          </div>
        )}

        {/* 游戏结束遮罩 */}
        {gameOver && (
          <div className="absolute inset-0 flex flex-col items-center justify-center rounded-lg bg-white/85 backdrop-blur-sm">
            <p className="text-base font-bold text-red-600">无法移动</p>
            <p className="mt-1 text-xs text-pine-700">最终得分: {score}</p>
            <button
              onClick={resetGame}
              className="mt-3 rounded-lg bg-magic-600 px-4 py-1.5 text-xs font-medium text-white transition-colors hover:bg-magic-700"
            >
              再来一局
            </button>
          </div>
        )}
      </div>

      {/* 重置按钮 */}
      <button
        onClick={resetGame}
        className="flex items-center gap-1.5 rounded-lg border border-pine-200 bg-white px-3 py-1.5 text-xs font-medium text-pine-700 transition-colors hover:bg-pine-50"
      >
        <RotateCcw className="h-3.5 w-3.5" />
        重置
      </button>

      {/* 操作提示 */}
      <p className="text-[10px] text-pine-700/70">
        方向键 / WASD 移动 · 相同数字合并
      </p>
    </div>
  );
}
