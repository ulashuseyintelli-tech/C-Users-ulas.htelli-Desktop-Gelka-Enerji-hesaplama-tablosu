import { describe, it, expect } from 'vitest';
import { parseTurkishNumber } from '../numberFormat';

describe('parseTurkishNumber', () => {
  describe('Turkish format (dot=thousands, comma=decimal)', () => {
    it('parses "3,08" as 3.08', () => {
      expect(parseTurkishNumber('3,08')).toBe(3.08);
    });

    it('parses "32.257,08" as 32257.08', () => {
      expect(parseTurkishNumber('32.257,08')).toBe(32257.08);
    });

    it('parses "194.412,847" as 194412.847', () => {
      expect(parseTurkishNumber('194.412,847')).toBe(194412.847);
    });

    it('parses "0,895372" as 0.895372', () => {
      expect(parseTurkishNumber('0,895372')).toBe(0.895372);
    });

    it('parses "87.525,085" as 87525.085', () => {
      expect(parseTurkishNumber('87.525,085')).toBe(87525.085);
    });

    it('parses "1.234.567,89" as 1234567.89', () => {
      expect(parseTurkishNumber('1.234.567,89')).toBe(1234567.89);
    });
  });

  describe('Single dot, no comma (preserved per user rule)', () => {
    it('parses "1.21167" as 1.21167', () => {
      expect(parseTurkishNumber('1.21167')).toBe(1.21167);
    });

    it('parses "194412.847" as 194412.847', () => {
      expect(parseTurkishNumber('194412.847')).toBe(194412.847);
    });

    it('parses "3.08" as 3.08', () => {
      expect(parseTurkishNumber('3.08')).toBe(3.08);
    });
  });

  describe('Multiple dots, no comma (all thousands)', () => {
    it('parses "1.234.567" as 1234567', () => {
      expect(parseTurkishNumber('1.234.567')).toBe(1234567);
    });
  });

  describe('Plain integers', () => {
    it('parses "194412" as 194412', () => {
      expect(parseTurkishNumber('194412')).toBe(194412);
    });

    it('parses "0" as 0', () => {
      expect(parseTurkishNumber('0')).toBe(0);
    });
  });

  describe('Empty / blank / invalid → undefined', () => {
    it('returns undefined for empty string', () => {
      expect(parseTurkishNumber('')).toBeUndefined();
    });

    it('returns undefined for whitespace only', () => {
      expect(parseTurkishNumber('   ')).toBeUndefined();
    });

    it('returns undefined for null', () => {
      expect(parseTurkishNumber(null)).toBeUndefined();
    });

    it('returns undefined for undefined', () => {
      expect(parseTurkishNumber(undefined)).toBeUndefined();
    });

    it('returns undefined for non-numeric text', () => {
      expect(parseTurkishNumber('abc')).toBeUndefined();
    });

    it('returns undefined for "12abc"', () => {
      expect(parseTurkishNumber('12abc')).toBeUndefined();
    });
  });

  describe('Whitespace tolerant', () => {
    it('trims leading/trailing whitespace', () => {
      expect(parseTurkishNumber('  3,08  ')).toBe(3.08);
    });
  });

  describe('Never returns NaN', () => {
    it('returns undefined instead of NaN for garbage', () => {
      const result = parseTurkishNumber('---');
      expect(result).toBeUndefined();
    });
  });
});
