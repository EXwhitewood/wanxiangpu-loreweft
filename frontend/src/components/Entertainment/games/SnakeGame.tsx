/**
 * 贪吃蛇小游戏 (Snake Game)
 * =========================
 *
 * 经典红白机风格贪吃蛇，使用 Canvas 渲染避免 React 重渲染开销。
 *
 * 设计要点
 * --------
 * - 网格 20x20，每格 16px，画布 320x320
 * - 方向键 / WASD 控制，禁止反向移动
 * - 速度随分数提升（每 5 分加速一次）
 * - 撞墙或撞自己游戏结束，记录最高分到 localStorage
 * - 暂停/继续：空格键
 */

import { useEffect, useRef, useState, useCallback } from "react";
import { Play, Pause, RotateCcw } from "lucide-react";

// === 游戏常量 ===
const GRID_SIZE = 20;                    // 网格数量（20x20）
const CELL_SIZE = 16;                    // 每格像素
const CANVAS_SIZE = GRID_SIZE * CELL_SIZE;  // 画布总像素 320
const INITIAL_SPEED = 180;               // 初始帧间隔（ms）
const MIN_SPEED = 80;                    // 最快帧间隔（ms）
const SPEED_STEP = 8;                    // 每加速一次减少的间隔
const SPEED_UP_SCORE = 5;                // 每多少分加速一次
const STORAGE_KEY = "loreweft:snake_high_score";

type Point = { x: number; y: number };
type Direction = "up" | "down" | "left" | "right";

const DIRECTION_VECTORS: Record<Direction, Point> = {
  up: { x: 0, y: -1 },
  down: { x: 0, y: 1 },
  left: { x: -1, y: 0 },
  right: { x: 1, y: 0 },
};

const OPPOSITE_DIRECTION: Record<Direction, Direction> = {
  up: "down",
  down: "up",
  left: "right",
  right: "left",
};

// 随机生成食物位置（避开蛇身）
function randomFood(snake: Point[]): Point {
  const occupied = new Set(snake.map((p) => `${p.x},${p.y}`));
  const candidates: Point[] = [];
  for (let x = 0; x < GRID_SIZE; x++) {
    for (let y = 0; y < GRID_SIZE; y++) {
      if (!occupied.has(`${x},${y}`)) candidates.push({ x, y });
    }
  }
  if (candidates.length === 0) return { x: 0, y: 0 };
  return candidates[Math.floor(Math.random() * candidates.length)];
}

export default function SnakeGame() {
  const canvasRef = useRef<HTMLCanvasElement>(null);
  const snakeRef = useRef<Point[]>([{ x: 10, y: 10 }]);
  const directionRef = useRef<Direction>("right");
  const pendingDirectionRef = useRef<Direction>("right");
  const foodRef = useRef<Point>({ x: 5, y: 5 });
  const speedRef = useRef<number>(INITIAL_SPEED);
  const loopRef = useRef<number | null>(null);

  const [score, setScore] = useState(0);
  const [highScore, setHighScore] = useState(0);
  const [isPlaying, setIsPlaying] = useState(false);
  const [gameOver, setGameOver] = useState(false);

  // 初始化最高分
  useEffect(() => {
    const stored = localStorage.getItem(STORAGE_KEY);
    if (stored) setHighScore(parseInt(stored, 10) || 0);
  }, []);

  // 绘制当前游戏状态
  const draw = useCallback(() => {
    const canvas = canvasRef.current;
    if (!canvas) return;
    const ctx = canvas.getContext("2d");
    if (!ctx) return;

    // 背景
    ctx.fillStyle = "#fdfbf6";
    ctx.fillRect(0, 0, CANVAS_SIZE, CANVAS_SIZE);

    // 网格线（淡）
    ctx.strokeStyle = "rgba(84, 150, 136, 0.08)";
    ctx.lineWidth = 1;
    for (let i = 0; i <= GRID_SIZE; i++) {
      ctx.beginPath();
      ctx.moveTo(i * CELL_SIZE, 0);
      ctx.lineTo(i * CELL_SIZE, CANVAS_SIZE);
      ctx.stroke();
      ctx.beginPath();
      ctx.moveTo(0, i * CELL_SIZE);
      ctx.lineTo(CANVAS_SIZE, i * CELL_SIZE);
      ctx.stroke();
    }

    // 食物（红色圆点，对应红白机像素风）
    const food = foodRef.current;
    ctx.fillStyle = "#dc2626";
    ctx.beginPath();
    ctx.arc(
      food.x * CELL_SIZE + CELL_SIZE / 2,
      food.y * CELL_SIZE + CELL_SIZE / 2,
      CELL_SIZE / 2 - 2,
      0,
      Math.PI * 2,
    );
    ctx.fill();

    // 蛇身（pine 主色调）
    const snake = snakeRef.current;
    snake.forEach((segment, idx) => {
      // 蛇头颜色稍深
      ctx.fillStyle = idx === 0 ? "#3b7a6e" : "#549688";
      ctx.fillRect(
        segment.x * CELL_SIZE + 1,
        segment.y * CELL_SIZE + 1,
        CELL_SIZE - 2,
        CELL_SIZE - 2,
      );
    });
  }, []);

  // 单步游戏逻辑
  const tick = useCallback(() => {
    const snake = snakeRef.current;
    const direction = pendingDirectionRef.current;
    directionRef.current = direction;
    const vector = DIRECTION_VECTORS[direction];

    // 计算新蛇头位置
    const head = snake[0];
    const newHead: Point = { x: head.x + vector.x, y: head.y + vector.y };

    // 撞墙检测
    if (newHead.x < 0 || newHead.x >= GRID_SIZE || newHead.y < 0 || newHead.y >= GRID_SIZE) {
      setGameOver(true);
      setIsPlaying(false);
      return;
    }

    // 撞自己检测（注意：蛇尾本帧会移动，所以可以踩到原蛇尾位置）
    const willGrow = newHead.x === foodRef.current.x && newHead.y === foodRef.current.y;
    const bodyToCheck = willGrow ? snake : snake.slice(0, -1);
    if (bodyToCheck.some((p) => p.x === newHead.x && p.y === newHead.y)) {
      setGameOver(true);
      setIsPlaying(false);
      return;
    }

    // 更新蛇身
    const newSnake = [newHead, ...snake];
    if (willGrow) {
      // 吃到食物，不删尾巴
      foodRef.current = randomFood(newSnake);
      setScore((s) => {
        const newScore = s + 1;
        // 每 SPEED_UP_SCORE 分加速一次
        if (newScore % SPEED_UP_SCORE === 0) {
          speedRef.current = Math.max(MIN_SPEED, speedRef.current - SPEED_STEP);
        }
        return newScore;
      });
    } else {
      newSnake.pop();
    }
    snakeRef.current = newSnake;
    draw();
  }, [draw]);

  // 游戏主循环（速度变化时重建定时器）
  useEffect(() => {
    if (!isPlaying || gameOver) return;
    loopRef.current = window.setInterval(tick, speedRef.current);
    return () => {
      if (loopRef.current !== null) {
        clearInterval(loopRef.current);
        loopRef.current = null;
      }
    };
  }, [isPlaying, gameOver, tick, speedRef.current]);

  // 键盘控制
  useEffect(() => {
    const handleKey = (e: KeyboardEvent) => {
      const key = e.key.toLowerCase();
      let newDir: Direction | null = null;
      if (key === "arrowup" || key === "w") newDir = "up";
      else if (key === "arrowdown" || key === "s") newDir = "down";
      else if (key === "arrowleft" || key === "a") newDir = "left";
      else if (key === "arrowright" || key === "d") newDir = "right";
      else if (key === " ") {
        // 空格：暂停/继续
        e.preventDefault();
        if (!gameOver) setIsPlaying((p) => !p);
        return;
      }

      if (newDir) {
        e.preventDefault();
        // 禁止反向移动（基于当前实际方向，不是 pending）
        if (newDir !== OPPOSITE_DIRECTION[directionRef.current]) {
          pendingDirectionRef.current = newDir;
        }
      }
    };
    window.addEventListener("keydown", handleKey);
    return () => window.removeEventListener("keydown", handleKey);
  }, [gameOver]);

  // 游戏结束时更新最高分
  useEffect(() => {
    if (gameOver && score > highScore) {
      setHighScore(score);
      localStorage.setItem(STORAGE_KEY, String(score));
    }
  }, [gameOver, score, highScore]);

  // 重置游戏
  const resetGame = useCallback(() => {
    snakeRef.current = [{ x: 10, y: 10 }];
    directionRef.current = "right";
    pendingDirectionRef.current = "right";
    foodRef.current = randomFood(snakeRef.current);
    speedRef.current = INITIAL_SPEED;
    setScore(0);
    setGameOver(false);
    setIsPlaying(false);
    draw();
  }, [draw]);

  // 开始/暂停切换
  const togglePlay = useCallback(() => {
    if (gameOver) {
      resetGame();
      setIsPlaying(true);
    } else {
      setIsPlaying((p) => !p);
    }
  }, [gameOver, resetGame]);

  // 初始绘制
  useEffect(() => {
    draw();
  }, [draw]);

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

      {/* 画布 */}
      <div className="relative">
        <canvas
          ref={canvasRef}
          width={CANVAS_SIZE}
          height={CANVAS_SIZE}
          className="rounded-lg border border-pine-200 shadow-sm"
        />
        {/* 游戏结束遮罩 */}
        {gameOver && (
          <div className="absolute inset-0 flex flex-col items-center justify-center rounded-lg bg-white/80 backdrop-blur-sm">
            <p className="text-base font-bold text-red-600">游戏结束</p>
            <p className="mt-1 text-xs text-pine-700">得分: {score}</p>
            <button
              onClick={resetGame}
              className="mt-3 rounded-lg bg-magic-600 px-4 py-1.5 text-xs font-medium text-white transition-colors hover:bg-magic-700"
            >
              再来一局
            </button>
          </div>
        )}
        {/* 暂停遮罩 */}
        {!isPlaying && !gameOver && (
          <div className="absolute inset-0 flex items-center justify-center rounded-lg bg-white/60 backdrop-blur-sm">
            <span className="text-xs font-medium text-pine-700">按空格或下方按钮开始</span>
          </div>
        )}
      </div>

      {/* 控制按钮 */}
      <div className="flex gap-2">
        <button
          onClick={togglePlay}
          className="flex items-center gap-1.5 rounded-lg bg-magic-600 px-3 py-1.5 text-xs font-medium text-white transition-colors hover:bg-magic-700"
        >
          {isPlaying ? <Pause className="h-3.5 w-3.5" /> : <Play className="h-3.5 w-3.5" />}
          {isPlaying ? "暂停" : gameOver ? "重开" : "开始"}
        </button>
        <button
          onClick={resetGame}
          className="flex items-center gap-1.5 rounded-lg border border-pine-200 bg-white px-3 py-1.5 text-xs font-medium text-pine-700 transition-colors hover:bg-pine-50"
        >
          <RotateCcw className="h-3.5 w-3.5" />
          重置
        </button>
      </div>

      {/* 操作提示 */}
      <p className="text-[10px] text-pine-700/70">
        方向键 / WASD 移动 · 空格暂停
      </p>
    </div>
  );
}
