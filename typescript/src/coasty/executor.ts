/**
 * Defensive action executor.
 *
 * The docs describe TWO param shapes for several action types (the Reference
 * §6 table vs the local-automation section). This dispatcher accepts BOTH:
 *
 *   - key_press: `{key}`           OR `{keys: [...]}` (pressed in order)
 *   - wait:      `{ms}`            OR `{seconds}`
 *   - scroll:    `{direction, amount, x?, y?}` OR signed `{clicks}` (+up/-down)
 *   - drag:      `{from_x, from_y, to_x, to_y}` OR `{x1, y1, x2, y2}`
 *   - click:     `{x, y}` plus optional `{button?, clicks?}`
 *
 * It also:
 *   - scales coordinates by (real / sent) factors — coordinates come back in
 *     the space of the screenshot you SENT;
 *   - NEVER executes the `raw` action type (pyautogui source): it logs and
 *     skips it instead;
 *   - throws loudly on missing required params (no silent failures).
 *
 * A concrete Playwright backend plugs in by implementing `ExecutorBackend`
 * (example 01 ships one: page.mouse.click / page.keyboard.type / ...).
 * `NullBackend` records calls without touching any real screen.
 */
import { type Action, type ActionType } from './types.js';

export type MouseButton = 'left' | 'right' | 'middle';
export type ScrollDirection = 'up' | 'down' | 'left' | 'right';

export interface ClickOptions {
  button: MouseButton;
  clicks: number;
}

export interface ScrollOptions {
  direction: ScrollDirection;
  /** Always positive; the direction carries the sign. */
  amount: number;
  /** Scaled pointer position, when the action provided one. */
  x: number | null;
  y: number | null;
}

/**
 * The surface a real automation target implements. For a Playwright page:
 * click -> page.mouse.click, typeText -> page.keyboard.type, keyCombo ->
 * page.keyboard.press('Control+C'), scroll -> page.mouse.wheel, drag ->
 * mouse.move/down/move/up, wait -> page.waitForTimeout.
 */
export interface ExecutorBackend {
  click(x: number, y: number, options: ClickOptions): void | Promise<void>;
  move(x: number, y: number): void | Promise<void>;
  typeText(text: string): void | Promise<void>;
  /** Press a single key (called once per key for key_press lists). */
  keyPress(key: string): void | Promise<void>;
  /** Press a chord (keys held together). */
  keyCombo(keys: readonly string[]): void | Promise<void>;
  scroll(options: ScrollOptions): void | Promise<void>;
  drag(fromX: number, fromY: number, toX: number, toY: number): void | Promise<void>;
  wait(ms: number): void | Promise<void>;
}

export type ExecutorLogger = (message: string) => void;

export interface ExecuteOptions {
  /** Multiply model x by this (real_width / sent_width). Default 1. */
  scaleX?: number;
  /** Multiply model y by this (real_height / sent_height). Default 1. */
  scaleY?: number;
  /** Receives the skip notice for `raw` actions. Defaults to console.warn. */
  logger?: ExecutorLogger;
}

export interface ExecuteResult {
  actionType: ActionType;
  /** True when a backend method was invoked. */
  executed: boolean;
  /** Set for the `done` / `fail` marker actions — stop the loop. */
  terminal: 'done' | 'fail' | null;
  /** fail reason / raw-skip note, when applicable. */
  detail: string | null;
}

/** Thrown when an action is missing required params or has an unknown type. */
export class ActionParamError extends Error {
  readonly actionType: string;

  constructor(actionType: string, message: string) {
    super(`Invalid "${actionType}" action: ${message}`);
    this.name = 'ActionParamError';
    this.actionType = actionType;
  }
}

function asFiniteNumber(value: unknown): number | undefined {
  return typeof value === 'number' && Number.isFinite(value) ? value : undefined;
}

function firstNumber(
  params: Record<string, unknown>,
  names: readonly string[],
): number | undefined {
  for (const name of names) {
    const value = asFiniteNumber(params[name]);
    if (value !== undefined) return value;
  }
  return undefined;
}

function requireNumber(
  actionType: string,
  params: Record<string, unknown>,
  names: readonly string[],
): number {
  const value = firstNumber(params, names);
  if (value === undefined) {
    throw new ActionParamError(actionType, `missing numeric param (${names.join(' | ')})`);
  }
  return value;
}

function stringList(value: unknown): string[] | undefined {
  if (typeof value === 'string') return [value];
  if (Array.isArray(value) && value.every((item): item is string => typeof item === 'string')) {
    return value;
  }
  return undefined;
}

const MOUSE_BUTTONS: ReadonlySet<string> = new Set(['left', 'right', 'middle']);
const SCROLL_DIRECTIONS: ReadonlySet<string> = new Set(['up', 'down', 'left', 'right']);

function normalizeScroll(params: Record<string, unknown>): {
  direction: ScrollDirection;
  amount: number;
} {
  const rawDirection = params.direction;
  const direction =
    typeof rawDirection === 'string' && SCROLL_DIRECTIONS.has(rawDirection)
      ? (rawDirection as ScrollDirection)
      : undefined;
  const amount = asFiniteNumber(params.amount);
  const clicks = asFiniteNumber(params.clicks);

  if (direction !== undefined) {
    const magnitude = amount ?? (clicks !== undefined ? Math.abs(clicks) : undefined);
    if (magnitude === undefined) {
      throw new ActionParamError('scroll', 'direction given without "amount" or "clicks"');
    }
    return { direction, amount: Math.abs(magnitude) };
  }
  if (clicks !== undefined) {
    // pyautogui convention: positive scrolls up, negative scrolls down.
    return { direction: clicks >= 0 ? 'up' : 'down', amount: Math.abs(clicks) };
  }
  throw new ActionParamError('scroll', 'requires {direction, amount} or signed {clicks}');
}

/**
 * Execute one model action against a backend. Returns what happened; throws
 * {@link ActionParamError} on malformed/unknown actions. `raw` is never
 * executed — it is logged and skipped.
 */
export async function executeAction(
  action: Action,
  backend: ExecutorBackend,
  options: ExecuteOptions = {},
): Promise<ExecuteResult> {
  const scaleX = options.scaleX ?? 1;
  const scaleY = options.scaleY ?? 1;
  const logger = options.logger ?? ((message: string) => console.warn(message));
  const params = action.params ?? {};
  const type = action.action_type;

  const sx = (value: number): number => Math.round(value * scaleX);
  const sy = (value: number): number => Math.round(value * scaleY);
  const result = (executed: boolean, detail: string | null = null): ExecuteResult => ({
    actionType: type,
    executed,
    terminal: null,
    detail,
  });

  switch (type) {
    case 'click': {
      const x = requireNumber(type, params, ['x']);
      const y = requireNumber(type, params, ['y']);
      const rawButton = params.button;
      const button: MouseButton =
        typeof rawButton === 'string' && MOUSE_BUTTONS.has(rawButton)
          ? (rawButton as MouseButton)
          : 'left';
      const clicks = asFiniteNumber(params.clicks) ?? 1;
      await backend.click(sx(x), sy(y), { button, clicks: Math.max(1, Math.trunc(clicks)) });
      return result(true);
    }
    case 'move': {
      await backend.move(
        sx(requireNumber(type, params, ['x'])),
        sy(requireNumber(type, params, ['y'])),
      );
      return result(true);
    }
    case 'type_text': {
      const text = params.text;
      if (typeof text !== 'string') throw new ActionParamError(type, 'missing string param "text"');
      await backend.typeText(text);
      return result(true);
    }
    case 'key_press': {
      // Documented shapes: {key: "enter"} or {keys: [...]} pressed in order.
      const keys = stringList(params.key) ?? stringList(params.keys);
      if (keys === undefined || keys.length === 0) {
        throw new ActionParamError(type, 'requires "key" (string) or "keys" (string[])');
      }
      for (const key of keys) await backend.keyPress(key);
      return result(true);
    }
    case 'key_combo': {
      const keys = stringList(params.keys) ?? stringList(params.key);
      if (keys === undefined || keys.length === 0) {
        throw new ActionParamError(type, 'requires "keys" (string[])');
      }
      await backend.keyCombo(keys);
      return result(true);
    }
    case 'scroll': {
      const { direction, amount } = normalizeScroll(params);
      const x = firstNumber(params, ['x']);
      const y = firstNumber(params, ['y']);
      await backend.scroll({
        direction,
        amount,
        x: x === undefined ? null : sx(x),
        y: y === undefined ? null : sy(y),
      });
      return result(true);
    }
    case 'drag': {
      const fromX = requireNumber(type, params, ['from_x', 'x1']);
      const fromY = requireNumber(type, params, ['from_y', 'y1']);
      const toX = requireNumber(type, params, ['to_x', 'x2']);
      const toY = requireNumber(type, params, ['to_y', 'y2']);
      await backend.drag(sx(fromX), sy(fromY), sx(toX), sy(toY));
      return result(true);
    }
    case 'wait': {
      const ms = firstNumber(params, ['ms']);
      const seconds = firstNumber(params, ['seconds']);
      if (ms === undefined && seconds === undefined) {
        throw new ActionParamError(type, 'requires "ms" or "seconds"');
      }
      await backend.wait(ms ?? (seconds as number) * 1000);
      return result(true);
    }
    case 'done':
      return { actionType: type, executed: false, terminal: 'done', detail: null };
    case 'fail': {
      const reason = typeof params.reason === 'string' ? params.reason : null;
      return { actionType: type, executed: false, terminal: 'fail', detail: reason };
    }
    case 'raw': {
      // SECURITY: never execute model-supplied pyautogui source by default.
      const code = typeof params.code === 'string' ? params.code : (action.raw_code ?? '');
      const preview = (code ?? '').slice(0, 120);
      const detail = `skipped raw action (never executed by default): ${preview}`;
      logger(`[coasty-executor] ${detail}`);
      return result(false, detail);
    }
    default:
      throw new ActionParamError(String(type), 'unknown action type');
  }
}

/**
 * Execute a batch of actions in order, stopping at the first terminal
 * (`done` / `fail`) marker.
 */
export async function executeActions(
  actions: readonly Action[],
  backend: ExecutorBackend,
  options: ExecuteOptions = {},
): Promise<ExecuteResult[]> {
  const results: ExecuteResult[] = [];
  for (const action of actions) {
    const outcome = await executeAction(action, backend, options);
    results.push(outcome);
    if (outcome.terminal !== null) break;
  }
  return results;
}

export interface RecordedCall {
  method: keyof ExecutorBackend;
  args: unknown[];
}

/**
 * A backend that records calls without touching any real screen — handy for
 * tests and `--dry-run` example modes.
 */
export class NullBackend implements ExecutorBackend {
  readonly calls: RecordedCall[] = [];

  click(x: number, y: number, options: ClickOptions): void {
    this.calls.push({ method: 'click', args: [x, y, options] });
  }
  move(x: number, y: number): void {
    this.calls.push({ method: 'move', args: [x, y] });
  }
  typeText(text: string): void {
    this.calls.push({ method: 'typeText', args: [text] });
  }
  keyPress(key: string): void {
    this.calls.push({ method: 'keyPress', args: [key] });
  }
  keyCombo(keys: readonly string[]): void {
    this.calls.push({ method: 'keyCombo', args: [keys] });
  }
  scroll(options: ScrollOptions): void {
    this.calls.push({ method: 'scroll', args: [options] });
  }
  drag(fromX: number, fromY: number, toX: number, toY: number): void {
    this.calls.push({ method: 'drag', args: [fromX, fromY, toX, toY] });
  }
  wait(ms: number): void {
    this.calls.push({ method: 'wait', args: [ms] });
  }
}
