/**
 * Typed builders + structural validation for the Coasty Workflow DSL
 * (dsl_version 2026-06-01).
 *
 * 9 step types: task, assert, if, loop, parallel, human_approval, retry,
 * succeed, fail. 13 condition ops. `CONDITION_OPS` is a readonly tuple so the
 * type IS the contract.
 */
import { type CuaVersion, type OnAwaitingHuman } from './types.js';

// ---------------------------------------------------------------------------
// Conditions (13 ops — injection-safe, no free-text eval)
// ---------------------------------------------------------------------------

export const COMPARISON_OPS = ['eq', 'ne', 'lt', 'gt', 'lte', 'gte', 'contains'] as const;
export const VALUE_OPS = ['truthy', 'falsy', 'exists'] as const;
export const COMPOSITE_OPS = ['and', 'or'] as const;
export const NEGATION_OPS = ['not'] as const;

/** All 13 documented condition ops. This tuple IS the contract. */
export const CONDITION_OPS = [
  ...COMPARISON_OPS,
  ...VALUE_OPS,
  ...COMPOSITE_OPS,
  ...NEGATION_OPS,
] as const;

export type ComparisonOp = (typeof COMPARISON_OPS)[number];
export type ValueOp = (typeof VALUE_OPS)[number];
export type CompositeOp = (typeof COMPOSITE_OPS)[number];
export type NegationOp = (typeof NEGATION_OPS)[number];
export type ConditionOp = (typeof CONDITION_OPS)[number];

/** Operands may be literals or `{{path}}` template strings. */
export type TemplateValue = string | number | boolean | null;

export interface ComparisonCondition {
  op: ComparisonOp;
  left: TemplateValue;
  right: TemplateValue;
}

export interface ValueCondition {
  op: ValueOp;
  value: TemplateValue;
}

export interface CompositeCondition {
  op: CompositeOp;
  conditions: Condition[];
}

export interface NotCondition {
  op: NegationOp;
  condition: Condition;
}

export type Condition = ComparisonCondition | ValueCondition | CompositeCondition | NotCondition;

// ---------------------------------------------------------------------------
// Steps (9 types)
// ---------------------------------------------------------------------------

export const STEP_TYPES = [
  'task',
  'assert',
  'if',
  'loop',
  'parallel',
  'human_approval',
  'retry',
  'succeed',
  'fail',
] as const;
export type StepType = (typeof STEP_TYPES)[number];

/** Step ids must match this pattern. */
export const STEP_ID_PATTERN = /^[A-Za-z0-9_-]{1,64}$/;
/** `save_as` may not shadow these namespaces. */
export const RESERVED_SAVE_AS = ['inputs', 'vars'] as const;

export const MAX_TOTAL_STEPS = 200;
export const MAX_NESTING_DEPTH = 8;
export const MAX_PARALLEL_BRANCHES = 16;
export const MIN_RETRY_ATTEMPTS = 1;
export const MAX_RETRY_ATTEMPTS = 20;

export interface TaskStep {
  id: string;
  type: 'task';
  /** Supports `{{inputs.x}}` / `{{vars.y}}` / `{{stepId.field}}` templating. */
  task: string;
  machine_id?: string;
  cua_version?: CuaVersion;
  instructions?: string;
  system_prompt?: string;
  max_steps?: number;
  /** Binds `{status, passed, result, run_id, steps, error}` under this name. */
  save_as?: string;
  on_awaiting_human?: OnAwaitingHuman;
}

export interface AssertStep {
  id: string;
  type: 'assert';
  condition: Condition;
  message?: string;
}

export interface IfStep {
  id: string;
  type: 'if';
  condition: Condition;
  then: WorkflowStep[];
  else?: WorkflowStep[];
}

export interface LoopStep {
  id: string;
  type: 'loop';
  /** Exactly one of `count` | `while` must be set. */
  count?: number;
  while?: Condition;
  body: WorkflowStep[];
  max_iterations?: number;
}

export interface ParallelStep {
  id: string;
  type: 'parallel';
  /** <= 16 branches; no human_approval/succeed/fail anywhere inside. */
  branches: WorkflowStep[][];
}

export interface HumanApprovalStep {
  id: string;
  type: 'human_approval';
  message?: string;
  timeout_seconds?: number;
}

export interface RetryStep {
  id: string;
  type: 'retry';
  body: WorkflowStep[];
  /** 1-20. */
  max_attempts: number;
}

export interface SucceedStep {
  id: string;
  type: 'succeed';
  output?: Record<string, unknown>;
}

export interface FailStep {
  id: string;
  type: 'fail';
  message?: string;
}

export type WorkflowStep =
  | TaskStep
  | AssertStep
  | IfStep
  | LoopStep
  | ParallelStep
  | HumanApprovalStep
  | RetryStep
  | SucceedStep
  | FailStep;

export interface WorkflowDefinition {
  steps: WorkflowStep[];
  output?: Record<string, unknown>;
}

// ---------------------------------------------------------------------------
// Step builders
// ---------------------------------------------------------------------------

export function definition(
  steps: WorkflowStep[],
  output?: Record<string, unknown>,
): WorkflowDefinition {
  return output === undefined ? { steps } : { steps, output };
}

export function task(
  id: string,
  taskText: string,
  options: Omit<TaskStep, 'id' | 'type' | 'task'> = {},
): TaskStep {
  return { id, type: 'task', task: taskText, ...options };
}

export function assertStep(id: string, condition: Condition, message?: string): AssertStep {
  return message === undefined
    ? { id, type: 'assert', condition }
    : { id, type: 'assert', condition, message };
}

export function ifStep(
  id: string,
  condition: Condition,
  thenSteps: WorkflowStep[],
  elseSteps?: WorkflowStep[],
): IfStep {
  return elseSteps === undefined
    ? { id, type: 'if', condition, then: thenSteps }
    : { id, type: 'if', condition, then: thenSteps, else: elseSteps };
}

export function loopCount(
  id: string,
  count: number,
  body: WorkflowStep[],
  maxIterations?: number,
): LoopStep {
  return maxIterations === undefined
    ? { id, type: 'loop', count, body }
    : { id, type: 'loop', count, body, max_iterations: maxIterations };
}

export function loopWhile(
  id: string,
  condition: Condition,
  body: WorkflowStep[],
  maxIterations?: number,
): LoopStep {
  return maxIterations === undefined
    ? { id, type: 'loop', while: condition, body }
    : { id, type: 'loop', while: condition, body, max_iterations: maxIterations };
}

export function parallel(id: string, branches: WorkflowStep[][]): ParallelStep {
  return { id, type: 'parallel', branches };
}

export function humanApproval(
  id: string,
  options: { message?: string; timeoutSeconds?: number } = {},
): HumanApprovalStep {
  const step: HumanApprovalStep = { id, type: 'human_approval' };
  if (options.message !== undefined) step.message = options.message;
  if (options.timeoutSeconds !== undefined) step.timeout_seconds = options.timeoutSeconds;
  return step;
}

export function retryStep(id: string, body: WorkflowStep[], maxAttempts: number): RetryStep {
  return { id, type: 'retry', body, max_attempts: maxAttempts };
}

export function succeed(id: string, output?: Record<string, unknown>): SucceedStep {
  return output === undefined ? { id, type: 'succeed' } : { id, type: 'succeed', output };
}

export function fail(id: string, message?: string): FailStep {
  return message === undefined ? { id, type: 'fail' } : { id, type: 'fail', message };
}

// ---------------------------------------------------------------------------
// Condition builders
// ---------------------------------------------------------------------------

function comparison(
  op: ComparisonOp,
  left: TemplateValue,
  right: TemplateValue,
): ComparisonCondition {
  return { op, left, right };
}

export const eq = (left: TemplateValue, right: TemplateValue): ComparisonCondition =>
  comparison('eq', left, right);
export const ne = (left: TemplateValue, right: TemplateValue): ComparisonCondition =>
  comparison('ne', left, right);
export const lt = (left: TemplateValue, right: TemplateValue): ComparisonCondition =>
  comparison('lt', left, right);
export const gt = (left: TemplateValue, right: TemplateValue): ComparisonCondition =>
  comparison('gt', left, right);
export const lte = (left: TemplateValue, right: TemplateValue): ComparisonCondition =>
  comparison('lte', left, right);
export const gte = (left: TemplateValue, right: TemplateValue): ComparisonCondition =>
  comparison('gte', left, right);
export const contains = (left: TemplateValue, right: TemplateValue): ComparisonCondition =>
  comparison('contains', left, right);

export const truthy = (value: TemplateValue): ValueCondition => ({ op: 'truthy', value });
export const falsy = (value: TemplateValue): ValueCondition => ({ op: 'falsy', value });
export const exists = (value: TemplateValue): ValueCondition => ({ op: 'exists', value });

export const and = (...conditions: Condition[]): CompositeCondition => ({ op: 'and', conditions });
export const or = (...conditions: Condition[]): CompositeCondition => ({ op: 'or', conditions });
export const not = (condition: Condition): NotCondition => ({ op: 'not', condition });

// ---------------------------------------------------------------------------
// Validation (documented limits, enforced at create / ad-hoc time)
// ---------------------------------------------------------------------------

export interface DslIssue {
  /** JSON-path-ish location, e.g. `steps[0].branches[1][0]`. */
  path: string;
  message: string;
}

/** Thrown by {@link assertValidDefinition} when a definition violates the DSL limits. */
export class WorkflowDslError extends Error {
  readonly issues: readonly DslIssue[];

  constructor(issues: readonly DslIssue[]) {
    const lines = issues.map((issue) => `  - ${issue.path}: ${issue.message}`).join('\n');
    super(`Invalid workflow definition (${String(issues.length)} issue(s)):\n${lines}`);
    this.name = 'WorkflowDslError';
    this.issues = issues;
  }
}

function isRecord(value: unknown): value is Record<string, unknown> {
  return typeof value === 'object' && value !== null && !Array.isArray(value);
}

interface WalkState {
  issues: DslIssue[];
  totalSteps: number;
  insideParallel: boolean;
}

const PARALLEL_FORBIDDEN: ReadonlySet<string> = new Set(['human_approval', 'succeed', 'fail']);

function validateCondition(condition: unknown, path: string, issues: DslIssue[]): void {
  if (!isRecord(condition)) {
    issues.push({ path, message: 'condition must be an object with an "op" field' });
    return;
  }
  const op = condition.op;
  if (typeof op !== 'string' || !(CONDITION_OPS as readonly string[]).includes(op)) {
    issues.push({
      path,
      message: `unknown condition op ${JSON.stringify(op)} (expected one of: ${CONDITION_OPS.join(', ')})`,
    });
    return;
  }
  if ((COMPARISON_OPS as readonly string[]).includes(op)) {
    if (!('left' in condition) || !('right' in condition)) {
      issues.push({ path, message: `op "${op}" requires "left" and "right"` });
    }
  } else if ((VALUE_OPS as readonly string[]).includes(op)) {
    if (!('value' in condition)) {
      issues.push({ path, message: `op "${op}" requires "value"` });
    }
  } else if ((COMPOSITE_OPS as readonly string[]).includes(op)) {
    const conditions = condition.conditions;
    if (!Array.isArray(conditions) || conditions.length === 0) {
      issues.push({ path, message: `op "${op}" requires a non-empty "conditions" array` });
    } else {
      conditions.forEach((nested, index) => {
        validateCondition(nested, `${path}.conditions[${String(index)}]`, issues);
      });
    }
  } else {
    // 'not'
    if (!('condition' in condition)) {
      issues.push({ path, message: 'op "not" requires "condition"' });
    } else {
      validateCondition(condition.condition, `${path}.condition`, issues);
    }
  }
}

function validateSteps(steps: unknown, path: string, depth: number, state: WalkState): void {
  if (!Array.isArray(steps)) {
    state.issues.push({ path, message: 'expected an array of steps' });
    return;
  }
  if (depth > MAX_NESTING_DEPTH) {
    state.issues.push({
      path,
      message: `steps nest deeper than the maximum of ${String(MAX_NESTING_DEPTH)} levels`,
    });
    return;
  }
  steps.forEach((step, index) => {
    validateStep(step, `${path}[${String(index)}]`, depth, state);
  });
}

function validateStep(step: unknown, path: string, depth: number, state: WalkState): void {
  state.totalSteps += 1;
  if (state.totalSteps === MAX_TOTAL_STEPS + 1) {
    state.issues.push({
      path,
      message: `definition exceeds the maximum of ${String(MAX_TOTAL_STEPS)} total steps (counting nested steps)`,
    });
  }
  if (!isRecord(step)) {
    state.issues.push({ path, message: 'step must be an object' });
    return;
  }

  const id = step.id;
  if (typeof id !== 'string' || !STEP_ID_PATTERN.test(id)) {
    state.issues.push({
      path,
      message: `step id ${JSON.stringify(id)} must match ${STEP_ID_PATTERN.source}`,
    });
  }

  const type = step.type;
  if (typeof type !== 'string' || !(STEP_TYPES as readonly string[]).includes(type)) {
    state.issues.push({
      path,
      message: `unknown step type ${JSON.stringify(type)} (expected one of: ${STEP_TYPES.join(', ')})`,
    });
    return;
  }

  if (state.insideParallel && PARALLEL_FORBIDDEN.has(type)) {
    state.issues.push({
      path,
      message: `step type "${type}" is not allowed inside a parallel branch`,
    });
  }

  switch (type) {
    case 'task': {
      if (typeof step.task !== 'string' || step.task.length === 0) {
        state.issues.push({ path, message: 'task step requires a non-empty "task" string' });
      }
      const saveAs = step.save_as;
      if (saveAs !== undefined) {
        if (typeof saveAs !== 'string') {
          state.issues.push({ path, message: '"save_as" must be a string' });
        } else if ((RESERVED_SAVE_AS as readonly string[]).includes(saveAs)) {
          state.issues.push({
            path,
            message: `"save_as" must not be a reserved namespace (${RESERVED_SAVE_AS.join(', ')})`,
          });
        }
      }
      break;
    }
    case 'assert': {
      validateCondition(step.condition, `${path}.condition`, state.issues);
      break;
    }
    case 'if': {
      validateCondition(step.condition, `${path}.condition`, state.issues);
      validateSteps(step.then, `${path}.then`, depth + 1, state);
      if (step.else !== undefined) validateSteps(step.else, `${path}.else`, depth + 1, state);
      break;
    }
    case 'loop': {
      const hasCount = step.count !== undefined;
      const hasWhile = step.while !== undefined;
      if (hasCount === hasWhile) {
        state.issues.push({
          path,
          message: 'loop step requires exactly one of "count" or "while"',
        });
      }
      if (
        hasCount &&
        (typeof step.count !== 'number' || !Number.isInteger(step.count) || step.count < 1)
      ) {
        state.issues.push({ path, message: '"count" must be a positive integer' });
      }
      if (hasWhile) validateCondition(step.while, `${path}.while`, state.issues);
      validateSteps(step.body, `${path}.body`, depth + 1, state);
      break;
    }
    case 'parallel': {
      const branches = step.branches;
      if (!Array.isArray(branches)) {
        state.issues.push({ path, message: 'parallel step requires a "branches" array of arrays' });
        break;
      }
      if (branches.length > MAX_PARALLEL_BRANCHES) {
        state.issues.push({
          path,
          message: `parallel step has ${String(branches.length)} branches (maximum is ${String(MAX_PARALLEL_BRANCHES)})`,
        });
      }
      const wasInsideParallel = state.insideParallel;
      state.insideParallel = true;
      branches.forEach((branch, index) => {
        validateSteps(branch, `${path}.branches[${String(index)}]`, depth + 1, state);
      });
      state.insideParallel = wasInsideParallel;
      break;
    }
    case 'retry': {
      const attempts = step.max_attempts;
      if (
        typeof attempts !== 'number' ||
        !Number.isInteger(attempts) ||
        attempts < MIN_RETRY_ATTEMPTS ||
        attempts > MAX_RETRY_ATTEMPTS
      ) {
        state.issues.push({
          path,
          message: `"max_attempts" must be an integer between ${String(MIN_RETRY_ATTEMPTS)} and ${String(MAX_RETRY_ATTEMPTS)}`,
        });
      }
      validateSteps(step.body, `${path}.body`, depth + 1, state);
      break;
    }
    case 'human_approval':
    case 'succeed':
    case 'fail':
      break;
  }
}

/**
 * Structurally validate a workflow definition against the documented limits:
 * <= 200 total steps (nested counted), <= 8 nesting levels, <= 16 parallel
 * branches, retry attempts 1-20, no human_approval/succeed/fail inside
 * parallel, reserved save_as names, and the step-id regex.
 *
 * Returns a list of issues (empty = valid). Defensive: accepts arbitrary
 * runtime data without throwing.
 */
export function validateDefinition(def: WorkflowDefinition): DslIssue[] {
  const state: WalkState = { issues: [], totalSteps: 0, insideParallel: false };
  const raw: unknown = def;
  if (!isRecord(raw)) {
    return [{ path: '$', message: 'definition must be an object with a "steps" array' }];
  }
  validateSteps(raw.steps, 'steps', 1, state);
  return state.issues;
}

/** Like {@link validateDefinition} but throws {@link WorkflowDslError} on any issue. */
export function assertValidDefinition(def: WorkflowDefinition): void {
  const issues = validateDefinition(def);
  if (issues.length > 0) throw new WorkflowDslError(issues);
}
