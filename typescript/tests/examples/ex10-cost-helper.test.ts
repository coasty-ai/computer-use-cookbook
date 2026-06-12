/**
 * ex10 — cost helper: known-answer totals straight from the documented
 * pricing table (1 credit = $0.01 exactly), including the strict HD boundary,
 * the exactly-500-char prompt edge, per-minute floor on machine runtime, the
 * CLI flag mapping, and plan-mode batch totals.
 */
import { describe, expect, it } from 'vitest';

import {
  STANDARD_NOTES,
  UsageError,
  buildReportFromArgs,
  groundReport,
  machineReport,
  planReportFromJson,
  predictReport,
  renderReport,
  runReport,
  sessionReport,
  workflowReport,
} from '../../src/examples/ex10-cost-helper.js';

describe('predict known answers', () => {
  it('defaults (1920x1080, v3): 5 base + 1 HD = 6 cr ($0.06)', () => {
    const report = predictReport();
    expect(report.totalCredits).toBe(6);
    expect(report.totalUsd).toBe(0.06);
  });

  it('EDGE: exactly 1280x720 is NOT HD -> 5 cr', () => {
    expect(predictReport({ width: 1280, height: 720 }).totalCredits).toBe(5);
  });

  it('EDGE: 1281x720 and 1280x721 ARE HD -> 6 cr', () => {
    expect(predictReport({ width: 1281, height: 720 }).totalCredits).toBe(6);
    expect(predictReport({ width: 1280, height: 721 }).totalCredits).toBe(6);
  });

  it('3 HD trajectory shots: 5 + 3x2 + (1+3)x1 = 15 cr', () => {
    expect(predictReport({ trajectory: 3 }).totalCredits).toBe(15);
  });

  it('trajectory at non-HD resolution: 5 + 3x2 = 11 cr (no HD fee)', () => {
    expect(predictReport({ width: 1280, height: 720, trajectory: 3 }).totalCredits).toBe(11);
  });

  it('v1 engine + long prompt at 1280x720: 5 + 3 + 1 = 9 cr', () => {
    expect(
      predictReport({ width: 1280, height: 720, cuaVersion: 'v1', promptChars: 501 }).totalCredits,
    ).toBe(9);
  });

  it('EDGE: a system prompt of EXACTLY 500 chars is free', () => {
    expect(predictReport({ width: 1280, height: 720, promptChars: 500 }).totalCredits).toBe(5);
    expect(predictReport({ width: 1280, height: 720, promptChars: 501 }).totalCredits).toBe(6);
  });
});

describe('session known answers', () => {
  it('create only (0 steps): 10 cr, surcharge-free', () => {
    expect(sessionReport({ steps: 0, width: 1280, height: 720 }).totalCredits).toBe(10);
  });

  it('create + 3 non-HD steps: 10 + 3x4 = 22 cr', () => {
    expect(sessionReport({ steps: 3, width: 1280, height: 720 }).totalCredits).toBe(22);
  });

  it('create + 1 default (HD) step: 10 + 4 + 1 = 15 cr', () => {
    expect(sessionReport().totalCredits).toBe(15);
  });

  it('surcharges multiply per step: 2 v1 steps with 1 HD trajectory shot', () => {
    // 10 + 2x(4 + 1x2 + 2x1 + 3) = 10 + 22 = 32
    expect(sessionReport({ steps: 2, trajectory: 1, cuaVersion: 'v1' }).totalCredits).toBe(32);
  });
});

describe('ground / run / workflow known answers', () => {
  it('ground: 3 cr, +1 when HD', () => {
    expect(groundReport({ width: 1280, height: 720 }).totalCredits).toBe(3);
    expect(groundReport().totalCredits).toBe(4);
  });

  it('run: 10 steps x 5 cr = 50; v1 is 8 cr/step = 80', () => {
    expect(runReport({ steps: 10 }).totalCredits).toBe(50);
    expect(runReport({ steps: 10, cuaVersion: 'v1' }).totalCredits).toBe(80);
  });

  it('workflow: only task steps bill; control-flow line is 0 cr', () => {
    const report = workflowReport({ taskSteps: 7 });
    expect(report.totalCredits).toBe(35);
    const controlFlow = report.items.find((item) => item.label.includes('control-flow'));
    expect(controlFlow?.credits).toBe(0);
  });
});

describe('machine known answers', () => {
  it('linux 2h running: floor(5 x 120 / 60) = 10 cr', () => {
    expect(machineReport({ osType: 'linux', hours: 2 }).totalCredits).toBe(10);
  });

  it('EDGE: windows 1.5h running floors 13.5 -> 13 cr (per-minute, rounded down)', () => {
    expect(machineReport({ osType: 'windows', hours: 1.5 }).totalCredits).toBe(13);
  });

  it('EDGE: 5 minutes of linux runtime floors to 0 cr', () => {
    expect(machineReport({ osType: 'linux', hours: 5 / 60 }).totalCredits).toBe(0);
  });

  it('stopped hours bill at 1 cr/hr regardless of OS; snapshots add 1 cr each', () => {
    const report = machineReport({ osType: 'windows', hours: 1, stoppedHours: 2, snapshots: 2 });
    expect(report.totalCredits).toBe(9 + 2 + 2);
  });

  it('notes call out the $0.20 provisioning gate and per-minute floor', () => {
    const notes = machineReport().notes.join('\n');
    expect(notes).toContain('wallet >= 20 cr ($0.20)');
    expect(notes).toContain('rounded DOWN');
  });
});

describe('CLI flag mapping (buildReportFromArgs)', () => {
  it('predict --width/--height/--trajectory/--cua/--prompt-chars', () => {
    const report = buildReportFromArgs([
      'predict',
      '--width',
      '1280',
      '--height',
      '720',
      '--trajectory',
      '3',
      '--cua',
      'v1',
      '--prompt-chars',
      '501',
    ]);
    expect(report.totalCredits).toBe(5 + 6 + 3 + 1);
  });

  it('session --steps', () => {
    expect(
      buildReportFromArgs(['session', '--steps', '3', '--width', '1280', '--height', '720'])
        .totalCredits,
    ).toBe(22);
  });

  it('run/workflow step flags', () => {
    expect(buildReportFromArgs(['run', '--steps', '10']).totalCredits).toBe(50);
    expect(buildReportFromArgs(['workflow', '--task-steps', '7']).totalCredits).toBe(35);
  });

  it('machine --windows is shorthand for --os windows', () => {
    expect(buildReportFromArgs(['machine', '--windows', '--hours', '1']).totalCredits).toBe(9);
    expect(buildReportFromArgs(['machine', '--os', 'windows', '--hours', '1']).totalCredits).toBe(
      9,
    );
    expect(buildReportFromArgs(['machine', '--hours', '1']).totalCredits).toBe(5); // linux default
  });

  it('rejects unknown subcommands, bad numbers, and bad enums with UsageError', () => {
    expect(() => buildReportFromArgs([])).toThrow(UsageError);
    expect(() => buildReportFromArgs(['teleport'])).toThrow(UsageError);
    expect(() => buildReportFromArgs(['predict', '--width', 'wide'])).toThrow(UsageError);
    expect(() => buildReportFromArgs(['predict', '--cua', 'v2'])).toThrow(UsageError);
    expect(() => buildReportFromArgs(['machine', '--os', 'beos'])).toThrow(UsageError);
    expect(() => buildReportFromArgs(['plan'])).toThrow(UsageError);
  });
});

describe('plan mode', () => {
  it('totals a JSON batch with one line per entry', () => {
    const plan = JSON.stringify([
      { kind: 'predict' }, // 6
      { kind: 'run', steps: 4 }, // 20
      { kind: 'machine', osType: 'linux', hours: 2 }, // 10
      { kind: 'ground', width: 1280, height: 720 }, // 3
    ]);
    const report = planReportFromJson(plan);
    expect(report.items).toHaveLength(4);
    expect(report.totalCredits).toBe(6 + 20 + 10 + 3);
    expect(report.totalUsd).toBe(0.39);
  });

  it('rejects malformed plans loudly', () => {
    expect(() => planReportFromJson('not json')).toThrow(UsageError);
    expect(() => planReportFromJson('{"kind":"predict"}')).toThrow(UsageError);
    expect(() => planReportFromJson('[{"kind":"teleport"}]')).toThrow(UsageError);
    expect(() => planReportFromJson('[42]')).toThrow(UsageError);
  });
});

describe('rendering + standard notes', () => {
  it('every report carries the sandbox-$0 and refund-on-failure notes', () => {
    for (const report of [predictReport(), runReport(), machineReport()]) {
      for (const note of STANDARD_NOTES) expect(report.notes).toContain(note);
    }
  });

  it('sandbox rendering labels the total $0.00', () => {
    const rendered = renderReport(predictReport(), true);
    expect(rendered).toContain('$0.00 — sandbox key, never bills');
    expect(rendered).toContain('note: charges are debited up front and auto-refunded on failure');
  });

  it('live rendering shows the real USD total', () => {
    expect(renderReport(runReport({ steps: 10 }), false)).toContain('$0.50');
  });
});
