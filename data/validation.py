"""
Data validation utilities.
"""
import math
import statistics
from typing import List, Dict, Tuple
from utils.helpers import safe_division


class DataQualityValidator:
    """Comprehensive data quality validation for candles."""
    
    def __init__(self):
        self.quality_threshold = 0.85
        self.anomaly_history = {}

    def comprehensive_data_validation(self, symbol: str, candles: List[Dict]) -> Dict:
        """Perform comprehensive data quality validation."""
        if not candles:
            return {'quality_score': 0, 'is_valid': False, 'issues': ['No data']}
        quality_metrics = {
            'completeness': self.calculate_completeness(candles),
            'consistency': self.check_consistency(candles),
            'anomaly_score': self.detect_anomalies(candles),
            'volume_quality': self.assess_volume_quality(candles),
            'price_integrity': self.check_price_integrity(candles)
        }
        quality_score = (
            quality_metrics['completeness'] * 0.25 +
            quality_metrics['consistency'] * 0.25 +
            (100 - quality_metrics['anomaly_score']) * 0.20 +
            quality_metrics['volume_quality'] * 0.15 +
            quality_metrics['price_integrity'] * 0.15
        )
        issues = self.identify_issues(quality_metrics)
        return {
            'quality_score': quality_score,
            'is_valid': quality_score >= 70,
            'issues': issues,
            'metrics': quality_metrics,
            'recommendation': 'TRADABLE' if quality_score >= 80 else 'CAUTION' if quality_score >= 70 else 'AVOID'
        }

    def calculate_completeness(self, candles: List[Dict]) -> float:
        """Calculate data completeness score."""
        required_fields = ['open', 'high', 'low', 'close', 'volume']
        complete_count = 0
        for candle in candles[-50:]:
            if all(field in candle and candle[field] is not None for field in required_fields):
                complete_count += 1
        return safe_division(complete_count, min(50, len(candles)), 0.0) * 100

    def check_consistency(self, candles: List[Dict]) -> float:
        """Check data consistency."""
        if len(candles) < 2:
            return 100.0
        inconsistencies = 0
        total_checks = 0
        for i in range(1, len(candles)):
            if candles[i]['ts'] <= candles[i-1]['ts']:
                inconsistencies += 1
            total_checks += 1
            if (candles[i]['high'] < candles[i]['low'] or
                candles[i]['open'] > candles[i]['high'] or
                candles[i]['open'] < candles[i]['low'] or
                candles[i]['close'] > candles[i]['high'] or
                candles[i]['close'] < candles[i]['low']):
                inconsistencies += 1
            total_checks += 1
        consistency_score = 100.0 - (inconsistencies / total_checks * 100) if total_checks > 0 else 100.0
        return max(0.0, consistency_score)

    def check_price_integrity(self, candles: List[Dict]) -> float:
        """Check price data integrity."""
        if not candles:
            return 0.0
        valid_candles = 0
        for candle in candles:
            try:
                if (candle['open'] > 0 and candle['high'] > 0 and
                    candle['low'] > 0 and candle['close'] > 0 and
                    candle['high'] >= candle['low'] and
                    candle['high'] >= max(candle['open'], candle['close']) and
                    candle['low'] <= min(candle['open'], candle['close'])):
                    valid_candles += 1
            except (KeyError, TypeError):
                continue
        return safe_division(valid_candles, len(candles), 0.0) * 100

    def identify_issues(self, quality_metrics: Dict) -> List[str]:
        """Identify data quality issues."""
        issues = []
        if quality_metrics['completeness'] < 90:
            issues.append(f"Low completeness ({quality_metrics['completeness']:.1f}%)")
        if quality_metrics['consistency'] < 95:
            issues.append(f"Data inconsistencies ({quality_metrics['consistency']:.1f}%)")
        if quality_metrics['anomaly_score'] > 20:
            issues.append(f"High anomaly score ({quality_metrics['anomaly_score']:.1f})")
        if quality_metrics['volume_quality'] < 80:
            issues.append(f"Poor volume data ({quality_metrics['volume_quality']:.1f}%)")
        if quality_metrics['price_integrity'] < 95:
            issues.append(f"Price integrity issues ({quality_metrics['price_integrity']:.1f}%)")
        return issues

    def detect_anomalies(self, candles: List[Dict]) -> float:
        """Detect anomalies in candle data."""
        if len(candles) < 10:
            return 0.0
        anomalies = 0
        total_checks = 0
        for i in range(1, len(candles)):
            try:
                current = candles[i]
                previous = candles[i-1]
                if previous['close'] > 0:
                    price_change = abs(current['close'] / previous['close'] - 1)
                    if price_change > 0.10:
                        anomalies += 1
                total_checks += 1
                if 'volume' in current and 'volume' in previous:
                    if previous['volume'] > 0:
                        volume_ratio = current['volume'] / previous['volume']
                        if volume_ratio > 5.0 or volume_ratio < 0.2:
                            anomalies += 1
                    total_checks += 1
                if current['close'] > 0:
                    range_ratio = (current['high'] - current['low']) / current['close']
                    if range_ratio > 0.15:
                        anomalies += 1
                total_checks += 1
            except (KeyError, ZeroDivisionError):
                continue
        if total_checks == 0:
            return 0.0
        anomaly_score = safe_division(anomalies, total_checks, 0.0) * 100
        return min(100, anomaly_score * 2)

    def assess_volume_quality(self, candles: List[Dict]) -> float:
        """Assess volume data quality."""
        if not candles:
            return 0.0
        volumes = []
        valid_volume_count = 0
        for candle in candles:
            volume = candle.get('volume', 0)
            if volume is not None and volume >= 0 and not math.isnan(volume) and not math.isinf(volume):
                volumes.append(volume)
                valid_volume_count += 1
        if valid_volume_count == 0:
            return 0.0
        completeness = safe_division(valid_volume_count, len(candles), 0.0)
        if len(volumes) < 2:
            return completeness * 100
        try:
            avg_volume = sum(volumes) / len(volumes)
            if avg_volume > 0:
                zero_volumes = sum(1 for v in volumes if v == 0)
                zero_ratio = safe_division(zero_volumes, len(volumes), 0.0)
                volume_variance = statistics.variance(volumes) / avg_volume if len(volumes) > 1 else 0
                consistency_score = max(0, 100 - (volume_variance * 100))
                zero_penalty = zero_ratio * 100
                quality_score = (completeness * 40) + (consistency_score * 0.4) - zero_penalty
                return max(0, min(100, quality_score))
        except Exception:
            pass
        return completeness * 60


def enhanced_validate_candles(candles: List[Dict]) -> Tuple[bool, List[Dict]]:
    """Enhanced candle validation with anomaly detection."""
    if not candles:
        return False, []
    valid_candles = []
    anomalies_detected = 0
    for i, candle in enumerate(candles):
        try:
            if not all(key in candle for key in ['open', 'high', 'low', 'close']):
                continue
            o, h, l, c = candle['open'], candle['high'], candle['low'], candle['close']
            if any(price <= 0 for price in [o, h, l, c]):
                continue
            if not (l <= o <= h and l <= c <= h):
                continue
            if i > 0:
                prev_candle = candles[i-1]
                if prev_candle['close'] > 0:
                    price_change = abs(c / prev_candle['close'] - 1)
                    if price_change > 0.5:
                        anomalies_detected += 1
                        continue
            valid_candles.append(candle)
        except (TypeError, KeyError, ValueError):
            continue
    data_quality = len(valid_candles) / len(candles) if candles else 0
    anomaly_ratio = anomalies_detected / len(candles) if candles else 0
    if data_quality < 0.7 or anomaly_ratio > 0.1:
        return False, valid_candles
    return True, valid_candles


def validate_candles(candles: List[Dict]) -> Tuple[bool, List[Dict]]:
    """Basic candle validation (alias for enhanced_validate_candles)."""
    return enhanced_validate_candles(candles)


# Global data validator instance
data_validator = DataQualityValidator()

__all__ = ['DataQualityValidator', 'data_validator', 'enhanced_validate_candles', 'validate_candles']

