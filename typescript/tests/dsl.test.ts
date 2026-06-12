/**
 * dsl.ts — the 9 step builders, the 13-op condition tuple, and validate()
 * enforcing every documented limit (<=200 steps, <=8 nesting, <=16 parallel
 * branches, retry 1-20, parallel-forbidden steps, reserved save_as, id regex).
 */
import { describe, expect, it } from 'vitest';

import {
  CONDITION_OPS,
  MAX_NESTING_DEPTH,
  MAX_PARALLEL_BRANCHES,
  MAX_TOTAL_STEPS,
  STEP_TYPES,
  WorkflowDslError,
  and,
  assertStep,
  assertValidDefinition,
  contains,
  definition,
  eq,
  exists,
  fail,
  falsy,
  gt,
  gte,
  humanApproval,
  ifStep,
  loopCount,
  loopWhile,
  lt,
  lte,
  ne,
  not,
  or,
  parallel,
  retryStep,
  succeed,
  task,
  truthy,
  validateDefinition,
  type WorkflowStep,
} from '../src/coasty/dsl.js';

describe('contract tuples', () => {
  it('exposes exactly the 13 documented condition ops', () => {
    expect(CONDITION_OPS).toEqual([
      'eq',
      'ne',
      'lt',
      'gt',
      'lte',
      'gte',
      'contains',
      'truthy',
      'falsy',
      'exists',
      'and',
      'or',
      'not',
    ]);
    expect(CONDITION_OPS).toHaveLength(13);
  });

  it('exposes exactly the 9 documented step types', () => {
    expect(STEP_TYPES).toEqual([
      'task',
      'assert',
      'if',
      'loop',
      'parallel',
      'human_approval',
      'retry',
      'succeed',
      'fail',
    ]);
    expect(STEP_TYPES).toHaveLength(9);
  });
});

describe('condition builders', () => {
  it('builds comparison conditions', () => {
    expect(eq('{{check.passed}}', true)).toEqual({
      op: 'eq',
      left: '{{check.passed}}',
      right: true,
    });
    expect(ne(1, 2)).toEqual({ op: 'ne', left: 1, right: 2 });
    expect(lt('{{vars.n}}', 10)).toEqual({ op: 'lt', left: '{{vars.n}}', right: 10 });
    expect(gt(5, 1)).toEqual({ op: 'gt', left: 5, right: 1 });
    expect(lte(1, 1)).toEqual({ op: 'lte', left: 1, right: 1 });
    expect(gte(2, 1)).toEqual({ op: 'gte', left: 2, right: 1 });
    expect(contains('{{step.result}}', 'ok')).toEqual({
      op: 'contains',
      left: '{{step.result}}',
      right: 'ok',
    });
  });

  it('builds value, composite, and negation conditions', () => {
    expect(truthy('{{x}}')).toEqual({ op: 'truthy', value: '{{x}}' });
    expect(falsy(null)).toEqual({ op: 'falsy', value: null });
    expect(exists('{{inputs.city}}')).toEqual({ op: 'exists', value: '{{inputs.city}}' });
    expect(and(truthy(1), falsy(0))).toEqual({
      op: 'and',
      conditions: [
        { op: 'truthy', value: 1 },
        { op: 'falsy', value: 0 },
      ],
    });
    expect(or(truthy(1))).toEqual({ op: 'or', conditions: [{ op: 'truthy', value: 1 }] });
    expect(not(truthy(1))).toEqual({ op: 'not', condition: { op: 'truthy', value: 1 } });
  });
});

describe('step builders', () => {
  it('builds each of the 9 step types in the documented wire shape', () => {
    expect(task('t1', 'Open the app', { save_as: 'opened', max_steps: 10 })).toEqual({
      id: 't1',
      type: 'task',
      task: 'Open the app',
      save_as: 'opened',
      max_steps: 10,
    });
    expect(assertStep('a1', truthy('{{opened.passed}}'), 'must open')).toEqual({
      id: 'a1',
      type: 'assert',
      condition: { op: 'truthy', value: '{{opened.passed}}' },
      message: 'must open',
    });
    expect(ifStep('i1', truthy(1), [fail('f1')], [succeed('s1')])).toEqual({
      id: 'i1',
      type: 'if',
      condition: { op: 'truthy', value: 1 },
      then: [{ id: 'f1', type: 'fail' }],
      else: [{ id: 's1', type: 'succeed' }],
    });
    expect(loopCount('l1', 3, [task('t', 'x')], 5)).toEqual({
      id: 'l1',
      type: 'loop',
      count: 3,
      body: [{ id: 't', type: 'task', task: 'x' }],
      max_iterations: 5,
    });
    expect(loopWhile('l2', truthy('{{vars.go}}'), [task('t', 'x')])).toEqual({
      id: 'l2',
      type: 'loop',
      while: { op: 'truthy', value: '{{vars.go}}' },
      body: [{ id: 't', type: 'task', task: 'x' }],
    });
    expect(parallel('p1', [[task('a', 'x')], [task('b', 'y')]])).toEqual({
      id: 'p1',
      type: 'parallel',
      branches: [[{ id: 'a', type: 'task', task: 'x' }], [{ id: 'b', type: 'task', task: 'y' }]],
    });
    expect(humanApproval('h1', { message: 'OK to spend?', timeoutSeconds: 600 })).toEqual({
      id: 'h1',
      type: 'human_approval',
      message: 'OK to spend?',
      timeout_seconds: 600,
    });
    expect(retryStep('r1', [task('t', 'x')], 3)).toEqual({
      id: 'r1',
      type: 'retry',
      body: [{ id: 't', type: 'task', task: 'x' }],
      max_attempts: 3,
    });
    expect(succeed('ok', { report: '{{export.result}}' })).toEqual({
      id: 'ok',
      type: 'succeed',
      output: { report: '{{export.result}}' },
    });
    expect(fail('bad', 'no data')).toEqual({ id: 'bad', type: 'fail', message: 'no data' });
  });

  it('definition() wraps steps and optional output', () => {
    expect(definition([task('t', 'x')])).toEqual({
      steps: [{ id: 't', type: 'task', task: 'x' }],
    });
    expect(definition([task('t', 'x')], { done: true })).toEqual({
      steps: [{ id: 't', type: 'task', task: 'x' }],
      output: { done: true },
    });
  });
});

describe('validateDefinition', () => {
  it('accepts a realistic valid definition', () => {
    const def = definition([
      task('export', 'Export the report', { save_as: 'export_result' }),
      assertStep('check', truthy('{{export_result.passed}}')),
      ifStep(
        'branch',
        eq('{{export_result.status}}', 'succeeded'),
        [succeed('ok', { report: 'done' })],
        [retryStep('again', [task('retry-export', 'Export the report')], 3)],
      ),
      loopCount('thrice', 3, [task('poll', 'Check the inbox')]),
      parallel('fanout', [[task('a', 'x')], [task('b', 'y')]]),
      humanApproval('gate', { message: 'continue?' }),
      fail('bad', 'should not reach'),
    ]);
    expect(validateDefinition(def)).toEqual([]);
  });

  it('flags invalid step ids', () => {
    const issues = validateDefinition(definition([task('has spaces!', 'x')]));
    expect(issues).toHaveLength(1);
    expect(issues[0]?.message).toContain('step id');
  });

  it('flags ids longer than 64 chars', () => {
    const issues = validateDefinition(definition([task('a'.repeat(65), 'x')]));
    expect(issues).toHaveLength(1);
  });

  it('flags unknown step types', () => {
    const def = { steps: [{ id: 'x', type: 'teleport' }] } as never;
    const issues = validateDefinition(def);
    expect(issues.some((i) => i.message.includes('unknown step type'))).toBe(true);
  });

  it('flags unknown condition ops with the documented list', () => {
    const def = {
      steps: [{ id: 'a', type: 'assert', condition: { op: 'matches', left: 1, right: 1 } }],
    } as never;
    const issues = validateDefinition(def);
    expect(issues[0]?.message).toContain('unknown condition op');
    expect(issues[0]?.message).toContain('eq, ne, lt, gt, lte, gte, contains');
  });

  it('flags comparison/value/composite/not conditions missing their fields', () => {
    const def = {
      steps: [
        { id: 'a', type: 'assert', condition: { op: 'eq', left: 1 } }, // missing right
        { id: 'b', type: 'assert', condition: { op: 'truthy' } }, // missing value
        { id: 'c', type: 'assert', condition: { op: 'and', conditions: [] } }, // empty
        { id: 'd', type: 'assert', condition: { op: 'not' } }, // missing condition
      ],
    } as never;
    const issues = validateDefinition(def);
    expect(issues).toHaveLength(4);
  });

  it('requires exactly one of count|while on loops', () => {
    const both = { ...loopCount('l', 2, [task('t', 'x')]), while: truthy(1) };
    const neither = { id: 'l', type: 'loop', body: [task('t', 'x')] } as never;
    expect(
      validateDefinition({ steps: [both] }).some((i) => i.message.includes('exactly one')),
    ).toBe(true);
    expect(
      validateDefinition({ steps: [neither] }).some((i) => i.message.includes('exactly one')),
    ).toBe(true);
  });

  it('bounds retry max_attempts to 1-20', () => {
    expect(validateDefinition(definition([retryStep('r', [task('t', 'x')], 0)]))).toHaveLength(1);
    expect(validateDefinition(definition([retryStep('r', [task('t', 'x')], 21)]))).toHaveLength(1);
    expect(validateDefinition(definition([retryStep('r', [task('t', 'x')], 1)]))).toEqual([]);
    expect(validateDefinition(definition([retryStep('r', [task('t', 'x')], 20)]))).toEqual([]);
  });

  it('caps parallel branches at 16', () => {
    const branches16 = Array.from({ length: 16 }, (_, i) => [task(`t${String(i)}`, 'x')]);
    const branches17 = Array.from({ length: 17 }, (_, i) => [task(`t${String(i)}`, 'x')]);
    expect(MAX_PARALLEL_BRANCHES).toBe(16);
    expect(validateDefinition(definition([parallel('p', branches16)]))).toEqual([]);
    const issues = validateDefinition(definition([parallel('p', branches17)]));
    expect(issues.some((i) => i.message.includes('17 branches'))).toBe(true);
  });

  it('forbids human_approval / succeed / fail inside parallel (even nested)', () => {
    for (const forbidden of [humanApproval('h'), succeed('s'), fail('f')]) {
      const direct = validateDefinition(definition([parallel('p', [[forbidden]])]));
      expect(direct.some((i) => i.message.includes('not allowed inside a parallel'))).toBe(true);

      const nested = validateDefinition(
        definition([parallel('p', [[ifStep('i', truthy(1), [forbidden])]])]),
      );
      expect(nested.some((i) => i.message.includes('not allowed inside a parallel'))).toBe(true);
    }
  });

  it('allows those step types outside parallel again after the parallel block', () => {
    const def = definition([parallel('p', [[task('t', 'x')]]), humanApproval('h'), succeed('s')]);
    expect(validateDefinition(def)).toEqual([]);
  });

  it('enforces the 8-level nesting limit', () => {
    const nest = (depth: number): WorkflowStep[] =>
      depth === 0
        ? [task('leaf', 'x')]
        : [ifStep(`if-${String(depth)}`, truthy(1), nest(depth - 1))];
    expect(MAX_NESTING_DEPTH).toBe(8);
    // 7 nested ifs -> innermost steps at depth 8: OK.
    expect(validateDefinition(definition(nest(7)))).toEqual([]);
    // 8 nested ifs -> innermost steps at depth 9: rejected.
    const issues = validateDefinition(definition(nest(8)));
    expect(issues.some((i) => i.message.includes('nest deeper'))).toBe(true);
  });

  it('enforces the 200-total-steps limit (counting nested steps)', () => {
    const steps200 = Array.from({ length: 200 }, (_, i) => task(`t${String(i)}`, 'x'));
    expect(MAX_TOTAL_STEPS).toBe(200);
    expect(validateDefinition(definition(steps200))).toEqual([]);

    const steps201 = [...steps200, task('one-more', 'x')];
    const issues = validateDefinition(definition(steps201));
    expect(issues).toHaveLength(1);
    expect(issues[0]?.message).toContain('200');

    // Nested steps count too: 199 top-level + a loop with 2 children = 202.
    const nested = definition([
      ...Array.from({ length: 199 }, (_, i) => task(`t${String(i)}`, 'x')),
      loopCount('l', 2, [task('a', 'x'), task('b', 'x')]),
    ]);
    expect(validateDefinition(nested).some((i) => i.message.includes('200'))).toBe(true);
  });

  it('rejects reserved save_as names (inputs, vars)', () => {
    for (const reserved of ['inputs', 'vars']) {
      const issues = validateDefinition(definition([task('t', 'x', { save_as: reserved })]));
      expect(issues.some((i) => i.message.includes('reserved'))).toBe(true);
    }
    expect(validateDefinition(definition([task('t', 'x', { save_as: 'result' })]))).toEqual([]);
  });

  it('requires a non-empty task string', () => {
    const issues = validateDefinition(definition([task('t', '')]));
    expect(issues.some((i) => i.message.includes('non-empty'))).toBe(true);
  });

  it('is defensive about non-object inputs', () => {
    expect(validateDefinition(null as never)).toHaveLength(1);
    expect(validateDefinition({ steps: 'nope' } as never)).toHaveLength(1);
    expect(validateDefinition({ steps: [42] } as never).length).toBeGreaterThan(0);
  });

  it('reports JSON-path-ish locations', () => {
    const def = definition([parallel('p', [[task('ok', 'x')], [succeed('nope')]])]);
    const issues = validateDefinition(def);
    expect(issues[0]?.path).toBe('steps[0].branches[1][0]');
  });
});

describe('assertValidDefinition', () => {
  it('passes silently for a valid definition', () => {
    expect(() => {
      assertValidDefinition(definition([task('t', 'x')]));
    }).not.toThrow();
  });

  it('throws WorkflowDslError carrying every issue', () => {
    const def = definition([task('bad id!', '', { save_as: 'vars' })]);
    try {
      assertValidDefinition(def);
      expect.unreachable('should have thrown');
    } catch (error) {
      expect(error).toBeInstanceOf(WorkflowDslError);
      const dslError = error as WorkflowDslError;
      expect(dslError.issues).toHaveLength(3);
      expect(dslError.message).toContain('3 issue(s)');
    }
  });
});
