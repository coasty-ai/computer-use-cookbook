/**
 * cost.ts — the full pricing table, with the documented edge cases:
 * the STRICT HD boundary (exactly 1280x720 is NOT HD), the 500-char
 * system-prompt boundary (exactly 500 is free), run steps 5/8, machine hourly
 * rates, and floor-rounded runtime metering.
 */
import { describe, expect, it } from 'vitest';

import {
  HD_HEIGHT_THRESHOLD,
  HD_WIDTH_THRESHOLD,
  PRICING,
  SYSTEM_PROMPT_FREE_CHARS,
  creditsToUsd,
  estimateGroundCredits,
  estimateMachineRuntimeCredits,
  estimatePredictCredits,
  estimateRunCredits,
  estimateSessionCreateCredits,
  estimateSessionPredictCredits,
  estimateParseCredits,
  estimateWorkflowRunCredits,
  formatEstimate,
  formatUsd,
  isHdImage,
  machineHourlyCredits,
  runStepCredits,
  snapshotCredits,
} from '../src/coasty/cost.js';

describe('isHdImage — strict boundary', () => {
  it('exactly 1280x720 is NOT HD', () => {
    expect(HD_WIDTH_THRESHOLD).toBe(1280);
    expect(HD_HEIGHT_THRESHOLD).toBe(720);
    expect(isHdImage(1280, 720)).toBe(false);
  });

  it('one pixel over either threshold IS HD', () => {
    expect(isHdImage(1281, 720)).toBe(true);
    expect(isHdImage(1280, 721)).toBe(true);
  });

  it('the API defaults (1920x1080) are HD', () => {
    expect(isHdImage(1920, 1080)).toBe(true);
  });
});

describe('estimatePredictCredits', () => {
  it('is 5 credits base at <=1280x720', () => {
    expect(estimatePredictCredits({ screenWidth: 1280, screenHeight: 720 })).toBe(5);
  });

  it('defaults to 1920x1080 — which IS HD (+1)', () => {
    expect(estimatePredictCredits()).toBe(6);
  });

  it('adds +2 per trajectory screenshot', () => {
    expect(
      estimatePredictCredits({ screenWidth: 1280, screenHeight: 720, trajectoryScreenshots: 3 }),
    ).toBe(5 + 3 * 2);
  });

  it('applies the HD fee to the current shot AND each trajectory shot', () => {
    expect(
      estimatePredictCredits({ screenWidth: 1920, screenHeight: 1080, trajectoryScreenshots: 2 }),
    ).toBe(5 + 2 * 2 + 1 * (1 + 2)); // 12
  });

  it('adds +3 for the v1 engine', () => {
    expect(estimatePredictCredits({ cuaVersion: 'v1', screenWidth: 1280, screenHeight: 720 })).toBe(
      8,
    );
    expect(estimatePredictCredits({ cuaVersion: 'v4', screenWidth: 1280, screenHeight: 720 })).toBe(
      5,
    );
  });

  it('system prompt: exactly 500 chars is FREE; 501 adds +1', () => {
    expect(SYSTEM_PROMPT_FREE_CHARS).toBe(500);
    const base = { screenWidth: 1280, screenHeight: 720 };
    expect(estimatePredictCredits({ ...base, systemPromptChars: 500 })).toBe(5);
    expect(estimatePredictCredits({ ...base, systemPromptChars: 501 })).toBe(6);
  });

  it('stacks every surcharge', () => {
    expect(
      estimatePredictCredits({
        cuaVersion: 'v1',
        screenWidth: 1920,
        screenHeight: 1080,
        trajectoryScreenshots: 1,
        systemPromptChars: 1000,
      }),
    ).toBe(5 + 2 + 1 * 2 + 3 + 1); // 13
  });
});

describe('sessions', () => {
  it('session create is a flat 10 credits with NO surcharges', () => {
    expect(estimateSessionCreateCredits()).toBe(10);
    expect(PRICING.sessionCreate).toBe(10);
  });

  it('session predict is 4 credits + the same surcharges as /predict', () => {
    expect(estimateSessionPredictCredits({ screenWidth: 1280, screenHeight: 720 })).toBe(4);
    expect(estimateSessionPredictCredits({ screenWidth: 1920, screenHeight: 1080 })).toBe(5);
    expect(
      estimateSessionPredictCredits({
        cuaVersion: 'v1',
        screenWidth: 1280,
        screenHeight: 720,
        trajectoryScreenshots: 2,
      }),
    ).toBe(4 + 4 + 3);
  });
});

describe('ground / parse', () => {
  it('ground is 3 credits, +1 only when HD', () => {
    expect(estimateGroundCredits({ screenWidth: 1280, screenHeight: 720 })).toBe(3);
    expect(estimateGroundCredits({ screenWidth: 1281, screenHeight: 720 })).toBe(4);
    expect(estimateGroundCredits()).toBe(4); // default 1920x1080 is HD
  });

  it('parse is free', () => {
    expect(estimateParseCredits()).toBe(0);
    expect(PRICING.parse).toBe(0);
  });
});

describe('run steps', () => {
  it('v3/v4 steps cost 5; v1 steps cost 8', () => {
    expect(runStepCredits('v3')).toBe(5);
    expect(runStepCredits('v4')).toBe(5);
    expect(runStepCredits('v1')).toBe(8);
    expect(runStepCredits()).toBe(5);
  });

  it('estimates whole runs and workflow task steps at the step rate', () => {
    expect(estimateRunCredits({ steps: 10 })).toBe(50);
    expect(estimateRunCredits({ steps: 10, cuaVersion: 'v1' })).toBe(80);
    expect(estimateWorkflowRunCredits({ taskSteps: 3 })).toBe(15);
    expect(estimateWorkflowRunCredits({ taskSteps: 3, cuaVersion: 'v1' })).toBe(24);
  });
});

describe('machines', () => {
  it('hourly rates: linux 5, windows 9 while running (incl. transitions)', () => {
    expect(machineHourlyCredits('linux', 'running')).toBe(5);
    expect(machineHourlyCredits('windows', 'running')).toBe(9);
    expect(machineHourlyCredits('linux', 'starting')).toBe(5);
    expect(machineHourlyCredits('windows', 'stopping')).toBe(9);
    expect(machineHourlyCredits('linux', 'restarting')).toBe(5);
  });

  it('stopped/suspended is 1/hr on ANY os', () => {
    expect(machineHourlyCredits('linux', 'stopped')).toBe(1);
    expect(machineHourlyCredits('windows', 'stopped')).toBe(1);
    expect(machineHourlyCredits('windows', 'suspended_for_billing')).toBe(1);
  });

  it('creating/error/terminated bill nothing', () => {
    expect(machineHourlyCredits('linux', 'creating')).toBe(0);
    expect(machineHourlyCredits('windows', 'error')).toBe(0);
    expect(machineHourlyCredits('linux', 'terminated')).toBe(0);
  });

  it('runtime is metered per minute, rounded DOWN', () => {
    expect(estimateMachineRuntimeCredits({ osType: 'linux', minutes: 60 })).toBe(5);
    expect(estimateMachineRuntimeCredits({ osType: 'linux', minutes: 30 })).toBe(2); // floor(2.5)
    expect(estimateMachineRuntimeCredits({ osType: 'windows', minutes: 90 })).toBe(13); // floor(13.5)
    expect(
      estimateMachineRuntimeCredits({ osType: 'windows', minutes: 59, status: 'stopped' }),
    ).toBe(0); // floor(59/60)
  });

  it('snapshot is 1 credit; provisioning gate is 20 credits (not a fee)', () => {
    expect(snapshotCredits()).toBe(1);
    expect(PRICING.provisioningGateCredits).toBe(20);
  });
});

describe('formatting', () => {
  it('1 credit = 1 cent = $0.01 exactly', () => {
    expect(creditsToUsd(1)).toBe(0.01);
    expect(creditsToUsd(123)).toBe(1.23);
    expect(formatUsd(5)).toBe('$0.05');
    expect(formatUsd(150)).toBe('$1.50');
  });

  it('formatEstimate renders an itemized table with a total', () => {
    const text = formatEstimate([
      { label: 'predict x3', credits: 15 },
      { label: 'session create', credits: 10 },
    ]);
    expect(text).toContain('Estimated cost:');
    expect(text).toContain('predict x3');
    expect(text).toContain('($0.25)');
    expect(text).toContain('TOTAL');
    expect(text).toContain('25 cr');
  });

  it('labels sandbox estimates as never billing', () => {
    const text = formatEstimate([{ label: 'predict', credits: 5 }], { sandbox: true });
    expect(text).toContain('sandbox key, never bills');
    expect(text).toContain('$0.00');
  });
});
