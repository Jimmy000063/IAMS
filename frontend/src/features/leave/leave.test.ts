/**
 * Leave quantity helpers.
 *
 * Worth testing because the failure is silent: leave quantities arrive as
 * decimal strings, and anything that routes them through a float can produce a
 * balance that is off by a hundredth of a day and will not reconcile against
 * the ledger.
 */

import { describe, expect, it } from 'vitest';

import { compareDays, formatDays, isPositive } from '@/features/leave/api';

describe('formatDays', () => {
  it('drops trailing zeros without losing the value', () => {
    expect(formatDays('2.00')).toBe('2');
    expect(formatDays('0.50')).toBe('0.5');
    expect(formatDays('12.25')).toBe('12.25');
    expect(formatDays('1.10')).toBe('1.1');
  });

  it('shows a dash rather than a misleading zero when there is no value', () => {
    expect(formatDays(null)).toBe('—');
    expect(formatDays(undefined)).toBe('—');
    expect(formatDays('')).toBe('—');
  });

  it('keeps a real zero as zero', () => {
    expect(formatDays('0.00')).toBe('0');
  });

  it('keeps a negative balance negative', () => {
    expect(formatDays('-2.50')).toBe('-2.5');
  });

  it('does not turn a negative zero into "-0"', () => {
    expect(formatDays('-0.00')).toBe('0');
  });
});

describe('compareDays', () => {
  it('orders quantities correctly', () => {
    expect(compareDays('2.00', '1.00')).toBeGreaterThan(0);
    expect(compareDays('1.00', '2.00')).toBeLessThan(0);
    expect(compareDays('1.50', '1.50')).toBe(0);
  });

  it('treats differently written equal values as equal', () => {
    expect(compareDays('2', '2.00')).toBe(0);
    expect(compareDays('0.5', '0.50')).toBe(0);
  });

  it('distinguishes a single hundredth', () => {
    /** The precision that matters: 0.01 of a day must not be rounded away. */
    expect(compareDays('1.01', '1.00')).toBeGreaterThan(0);
  });

  it('handles negatives', () => {
    expect(compareDays('-1.00', '1.00')).toBeLessThan(0);
    expect(compareDays('-1.00', '-2.00')).toBeGreaterThan(0);
  });
});

describe('isPositive', () => {
  it('is true only for a quantity above zero', () => {
    expect(isPositive('0.50')).toBe(true);
    expect(isPositive('0.01')).toBe(true);
    expect(isPositive('0.00')).toBe(false);
    expect(isPositive('-1.00')).toBe(false);
  });

  it('is false for a missing value', () => {
    expect(isPositive(null)).toBe(false);
    expect(isPositive(undefined)).toBe(false);
  });
});
