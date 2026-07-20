"""DataSource ABC 测试"""
import unittest
from datetime import date
from typing import Optional

import pandas as pd

from wyckoff.data.base import (
    DataSource,
    DataFormatError,
    DataGapError,
    FetchError,
    CacheMissError,
)


class MockSource(DataSource):
    """用于测试的模拟数据源"""
    
    def __init__(self, df: Optional[pd.DataFrame] = None):
        self._df = df or pd.DataFrame()
    
    def fetch(self, code: str, start_date: date, end_date: date, adjust: str = "qfq") -> pd.DataFrame:
        return self._df
    
    def name(self) -> str:
        return "MockSource"


class TestDataSourceABC(unittest.TestCase):
    """测试 DataSource 抽象基类"""
    
    def test_cannot_instantiate_abstract(self):
        """抽象基类不能直接实例化"""
        with self.assertRaises(TypeError):
            DataSource()
    
    def test_mock_source_is_instance(self):
        """实现类是 DataSource 实例"""
        mock = MockSource()
        self.assertIsInstance(mock, DataSource)
    
    def test_validate_response_success(self):
        """验证有效数据"""
        df = pd.DataFrame({
            "date": [date(2024, 1, 2), date(2024, 1, 3)],
            "code": ["600519", "600519"],
            "open": [100.0, 101.0],
            "high": [102.0, 103.0],
            "low": [99.0, 100.0],
            "close": [101.0, 102.0],
            "volume": [1000, 2000],
        })
        mock = MockSource()
        # 不应抛出异常
        mock.validate_response(df)
    
    def test_validate_response_missing_columns(self):
        """验证缺少必需列"""
        df = pd.DataFrame({
            "date": [date(2024, 1, 2)],
            "code": ["600519"],
            "open": [100.0],
            "high": [102.0],
            "low": [99.0],
            "close": [101.0],
            # 缺少 volume
        })
        mock = MockSource()
        with self.assertRaises(DataFormatError):
            mock.validate_response(df)
    
    def test_validate_response_empty(self):
        """验证空 DataFrame"""
        df = pd.DataFrame()
        mock = MockSource()
        with self.assertRaises(DataFormatError):
            mock.validate_response(df)
    
    def test_validate_response_date_type(self):
        """验证日期类型错误"""
        df = pd.DataFrame({
            "date": ["2024-01-02"],  # 字符串而非 date
            "code": ["600519"],
            "open": [100.0],
            "high": [102.0],
            "low": [99.0],
            "close": [101.0],
            "volume": [1000],
        })
        mock = MockSource()
        with self.assertRaises(DataFormatError):
            mock.validate_response(df)
    
    def test_validate_response_price_type(self):
        """验证价格类型错误"""
        df = pd.DataFrame({
            "date": [date(2024, 1, 2)],
            "code": ["600519"],
            "open": ["100"],  # 字符串而非 float
            "high": [102.0],
            "low": [99.0],
            "close": [101.0],
            "volume": [1000],
        })
        mock = MockSource()
        with self.assertRaises(DataFormatError):
            mock.validate_response(df)
    
    def test_validate_response_volume_type(self):
        """验证量能类型错误"""
        df = pd.DataFrame({
            "date": [date(2024, 1, 2)],
            "code": ["600519"],
            "open": [100.0],
            "high": [102.0],
            "low": [99.0],
            "close": [101.0],
            "volume": [1000.5],  # float 而非 int
        })
        mock = MockSource()
        with self.assertRaises(DataFormatError):
            mock.validate_response(df)
    
    def test_name_method(self):
        """测试 name() 方法"""
        mock = MockSource()
        self.assertEqual(mock.name(), "MockSource")


class TestExceptions(unittest.TestCase):
    """测试自定义异常"""
    
    def test_exceptions_inheritance(self):
        """异常继承关系正确"""
        self.assertTrue(issubclass(DataFormatError, Exception))
        self.assertTrue(issubclass(DataGapError, Exception))
        self.assertTrue(issubclass(FetchError, Exception))
        self.assertTrue(issubclass(CacheMissError, FetchError))
    
    def test_exception_messages(self):
        """异常消息正确"""
        e1 = DataFormatError("test format error")
        self.assertEqual(str(e1), "test format error")
        
        e2 = DataGapError("test gap error")
        self.assertEqual(str(e2), "test gap error")
        
        e3 = FetchError("test fetch error")
        self.assertEqual(str(e3), "test fetch error")
        
        e4 = CacheMissError("test cache miss")
        self.assertEqual(str(e4), "test cache miss")


if __name__ == "__main__":
    unittest.main()
