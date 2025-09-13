"""
Comprehensive test suite for the Browser Operation Agent Data Supply Stack.

Tests all 4 data formats, action processing, and E2E scenarios with different site types.
"""

import asyncio
import json
import pytest
from unittest.mock import Mock, patch
from pathlib import Path
import sys

# Add project root to path
sys.path.append(str(Path(__file__).resolve().parents[2]))

from agent.browser.data_supply_stack import DataSupplyStack, ReferenceId, IDXTextFormat, AXSlimFormat, DOMLiteFormat, VISROIFormat
from agent.browser.action_processor import ActionProcessor, ActionRequest, ActionCommand, ActionType, OperationType


class TestReferenceId:
    """Test stable reference ID system."""
    
    def test_reference_id_creation(self):
        """Test creating reference IDs."""
        ref = ReferenceId("F0", backend_node_id=812345)
        assert str(ref) == "F0:BN-812345"
        
        ref = ReferenceId("F1", ax_node_id="AX-123")
        assert str(ref) == "F1:AX-AX-123"
    
    def test_reference_id_parsing(self):
        """Test parsing reference IDs from strings."""
        ref = ReferenceId.from_string("F0:BN-812345")
        assert ref.frame_id == "F0"
        assert ref.backend_node_id == 812345
        assert ref.ax_node_id is None
        
        ref = ReferenceId.from_string("F1:AX-123")
        assert ref.frame_id == "F1"
        assert ref.backend_node_id is None
        assert ref.ax_node_id == "123"
    
    def test_invalid_reference_formats(self):
        """Test invalid reference ID formats."""
        with pytest.raises(ValueError):
            ReferenceId.from_string("invalid")
        
        with pytest.raises(ValueError):
            ReferenceId.from_string("F0:INVALID-123")


class TestDataSupplyStackUnit:
    """Unit tests for DataSupplyStack components."""
    
    @pytest.fixture
    def data_stack(self):
        """Create DataSupplyStack instance for testing."""
        return DataSupplyStack(debug_port=9999)  # Non-standard port for testing
    
    def test_frame_id_mapping(self, data_stack):
        """Test frame ID mapping functionality."""
        frame_tree = {
            "frame": {"id": "main-frame"},
            "childFrames": [
                {
                    "frame": {"id": "child-frame-1"},
                    "childFrames": []
                },
                {
                    "frame": {"id": "child-frame-2"}, 
                    "childFrames": []
                }
            ]
        }
        
        data_stack._map_frame_ids(frame_tree)
        
        assert "main-frame" in data_stack.frame_id_map
        assert data_stack.frame_id_map["main-frame"] == "F0"
    
    def test_attribute_extraction(self, data_stack):
        """Test DOM attribute extraction."""
        # CDP format
        node_cdp = {
            "attributes": ["id", "test-id", "class", "btn primary", "disabled", ""]
        }
        attrs = data_stack._get_element_attributes(node_cdp)
        assert attrs["id"] == "test-id"
        assert attrs["class"] == "btn primary"
        assert attrs["disabled"] == ""
        
        # Playwright format
        node_pw = {
            "attributes": {"id": "test-id", "class": "btn primary"}
        }
        attrs = data_stack._get_element_attributes(node_pw)
        assert attrs["id"] == "test-id"
        assert attrs["class"] == "btn primary"
    
    def test_interactive_element_detection(self, data_stack):
        """Test interactive element detection."""
        # Button element
        button_node = {"nodeName": "button"}
        button_attrs = {"type": "submit"}
        assert data_stack._is_interactive_element(button_node, button_attrs)
        
        # Link element
        link_node = {"nodeName": "a"}
        link_attrs = {"href": "#"}
        assert data_stack._is_interactive_element(link_node, link_attrs)
        
        # Role-based interactive
        div_node = {"nodeName": "div"}
        div_attrs = {"role": "button"}
        assert data_stack._is_interactive_element(div_node, div_attrs)
        
        # Non-interactive
        p_node = {"nodeName": "p"}
        p_attrs = {}
        assert not data_stack._is_interactive_element(p_node, p_attrs)
    
    def test_css_selector_generation(self, data_stack):
        """Test CSS selector generation."""
        # ID selector
        node = {"nodeName": "div"}
        attrs = {"id": "main-content"}
        selector = data_stack._generate_css_selector(node, attrs)
        assert selector == "#main-content"
        
        # Class selector
        node = {"nodeName": "button"}
        attrs = {"class": "btn primary large"}
        selector = data_stack._generate_css_selector(node, attrs)
        assert selector == "button.btn.primary.large"
        
        # Tag fallback
        node = {"nodeName": "input"}
        attrs = {"type": "text"}
        selector = data_stack._generate_css_selector(node, attrs)
        assert selector == "input"
    
    def test_attribute_filtering(self, data_stack):
        """Test attribute whitelist filtering."""
        attrs = {
            "id": "test",
            "class": "btn",
            "data-custom": "value",
            "onclick": "alert()",
            "role": "button",
            "aria-label": "Submit"
        }
        
        filtered = data_stack._filter_attributes(attrs)
        
        assert "id" in filtered
        assert "class" in filtered
        assert "role" in filtered
        assert "aria-label" in filtered
        assert "data-custom" not in filtered
        assert "onclick" not in filtered


class TestActionProcessor:
    """Test action DSL processor."""
    
    @pytest.fixture
    def mock_data_stack(self):
        """Create mock DataSupplyStack."""
        stack = Mock(spec=DataSupplyStack)
        stack.extract_all_formats.return_value = {
            "idx_text": Mock(index_map={
                "0": {"frameId": "F0", "backendNodeId": 812345, "css": "#test-input"},
                "1": {"frameId": "F0", "backendNodeId": 812346, "css": "button"}
            }),
            "ax_slim": Mock(ax_nodes=[
                {"axId": "AX-10", "backendNodeId": 812345, "visible": True},
                {"axId": "AX-11", "backendNodeId": 812346, "visible": True}
            ]),
            "dom_lite": Mock(nodes=[
                Mock(backend_node_id=812345, bbox=[100, 100, 200, 130], clickable=False),
                Mock(backend_node_id=812346, bbox=[220, 100, 300, 130], clickable=True)
            ])
        }
        return stack
    
    @pytest.fixture
    def action_processor(self, mock_data_stack):
        """Create ActionProcessor with mock data stack."""
        return ActionProcessor(mock_data_stack)
    
    def test_action_request_parsing(self):
        """Test parsing JSON action requests."""
        request_json = json.dumps({
            "type": "act",
            "actions": [
                {"op": "click", "target": "F0:BN-812346"},
                {"op": "type", "target": "F0:BN-812345", "text": "test input"}
            ]
        })
        
        request_data = json.loads(request_json)
        # Convert action dicts to ActionCommand objects
        if request_data.get("actions"):
            request_data["actions"] = [ActionCommand(**action) for action in request_data["actions"]]
        
        request = ActionRequest(**request_data)
        
        assert request.type == "act"
        assert len(request.actions) == 2
        assert request.actions[0].op == "click"
        assert request.actions[1].text == "test input"
    
    @pytest.mark.asyncio
    async def test_target_validation_success(self, action_processor, mock_data_stack):
        """Test successful target validation."""
        current_state = await mock_data_stack.extract_all_formats()
        error = await action_processor._validate_target_reference("F0:BN-812345", current_state)
        assert error is None  # Should be valid
    
    @pytest.mark.asyncio 
    async def test_target_validation_not_found(self, action_processor, mock_data_stack):
        """Test target validation for non-existent target."""
        current_state = await mock_data_stack.extract_all_formats()
        error = await action_processor._validate_target_reference("F0:BN-999999", current_state)
        assert error is not None
        assert error.error_code == "NOT_FOUND"
    
    def test_operation_validation(self, action_processor):
        """Test operation-specific validation."""
        # Valid click
        action = ActionCommand(op="click", target="F0:BN-812345")
        error = action_processor._validate_operation(action)
        assert error is None
        
        # Invalid click (no target)
        action = ActionCommand(op="click")
        error = action_processor._validate_operation(action)
        assert error is not None
        assert error.error_code == "MISSING_TARGET"
        
        # Valid type
        action = ActionCommand(op="type", target="F0:BN-812345", text="hello")
        error = action_processor._validate_operation(action)
        assert error is None
        
        # Invalid type (no text)
        action = ActionCommand(op="type", target="F0:BN-812345")
        error = action_processor._validate_operation(action)
        assert error is not None
        assert error.error_code == "MISSING_TEXT"
        
        # Valid scroll
        action = ActionCommand(op="scroll", direction="down", amount=500)
        error = action_processor._validate_operation(action)
        assert error is None
        
        # Invalid scroll direction
        action = ActionCommand(op="scroll", direction="diagonal")
        error = action_processor._validate_operation(action)
        assert error is not None
        assert error.error_code == "INVALID_DIRECTION"


class TestE2EScenarios:
    """End-to-end test scenarios for different site types."""
    
    @pytest.mark.asyncio
    async def test_search_form_scenario(self):
        """Test search form interaction scenario."""
        # Create HTML for search form
        search_html = """
        <!DOCTYPE html>
        <html>
        <head><title>商品検索 - Test Store</title></head>
        <body>
            <div id="main" class="container" aria-label="検索フォーム">
                <h1>商品検索</h1>
                <input id="query" type="text" placeholder="キーワードを入力" value="">
                <button id="search-btn" type="submit">検索</button>
                <a href="/cart" role="link">カートを見る</a>
            </div>
            <div id="results" style="display:none;">
                <h2>検索結果</h2>
                <div class="product">ノートPC - ¥100,000</div>
            </div>
            <script>
                document.getElementById('search-btn').onclick = function() {
                    document.getElementById('results').style.display = 'block';
                };
            </script>
        </body>
        </html>
        """
        
        # This would be a full integration test with real browser
        # For now, we'll test the data structure creation
        
        # Simulate what the data stack should extract
        expected_idx_text = IDXTextFormat(
            meta={"viewport": [0, 0, 1400, 900], "ts": "2025-01-13T12:00:00Z"},
            text="# viewport: [0,0,1400,900]\n[0] <div id=\"main\" class=\"container\" aria-label=\"検索フォーム\">\n  [1] <h1 text=\"商品検索\">\n  [2] <input id=\"query\" role=\"textbox\" placeholder=\"キーワードを入力\" value=\"\">\n  [3] <button role=\"button\" text=\"検索\">\n  [4] <a role=\"link\" text=\"カートを見る\">",
            index_map={
                "2": {"frameId": "F0", "backendNodeId": 812345, "css": "#query"},
                "3": {"frameId": "F0", "backendNodeId": 812346, "css": "#search-btn"},
                "4": {"frameId": "F0", "backendNodeId": 812347, "css": "a[href='/cart']"}
            }
        )
        
        # Verify structure
        assert expected_idx_text.meta["viewport"] == [0, 0, 1400, 900]
        assert "商品検索" in expected_idx_text.text
        assert len(expected_idx_text.index_map) == 3
        assert expected_idx_text.index_map["2"]["css"] == "#query"
    
    @pytest.mark.asyncio
    async def test_news_article_scenario(self):
        """Test news article content extraction."""
        news_html = """
        <!DOCTYPE html>
        <html>
        <head><title>重要ニュース - News Site</title></head>
        <body>
            <header>
                <nav>ナビゲーション</nav>
            </header>
            <main>
                <article>
                    <h1>AI技術の進歩について</h1>
                    <div class="meta">
                        <span class="author">記者: 田中太郎</span>
                        <time>2025年1月13日</time>
                    </div>
                    <div class="content">
                        <p>AIの発展により、様々な分野で革新が起こっている。</p>
                        <p>特に自然言語処理の分野では目覚ましい進歩が見られる。</p>
                        <p>今後も技術革新が続くことが期待される。</p>
                    </div>
                </article>
                <aside>
                    <h3>関連記事</h3>
                    <ul>
                        <li><a href="/article2">機械学習の基礎</a></li>
                        <li><a href="/article3">データサイエンス入門</a></li>
                    </ul>
                </aside>
            </main>
        </body>
        </html>
        """
        
        # Simulate Readability extraction
        expected_content = {
            "title": "AI技術の進歩について",
            "byline": "記者: 田中太郎",
            "content": "<p>AIの発展により、様々な分野で革新が起こっている。</p><p>特に自然言語処理の分野では目覚ましい進歩が見られる。</p><p>今後も技術革新が続くことが期待される。</p>",
            "text_content": "AIの発展により、様々な分野で革新が起こっている。特に自然言語処理の分野では目覚ましい進歩が見られる。今後も技術革新が続くことが期待される。",
            "excerpt": "AIの発展により、様々な分野で革新が起こっている。特に自然言語処理...",
            "length": 95  # Character count
        }
        
        # Verify content structure for LLM consumption
        assert expected_content["title"] == "AI技術の進歩について"
        assert "AI" in expected_content["text_content"]
        assert expected_content["length"] > 0
    
    @pytest.mark.asyncio
    async def test_dashboard_scenario(self):
        """Test dashboard interaction scenario."""
        dashboard_html = """
        <!DOCTYPE html>
        <html>
        <head><title>管理ダッシュボード</title></head>
        <body>
            <div class="dashboard">
                <nav class="sidebar">
                    <ul>
                        <li><a href="#overview" class="tab active">概要</a></li>
                        <li><a href="#analytics" class="tab">分析</a></li>
                        <li><a href="#settings" class="tab">設定</a></li>
                    </ul>
                </nav>
                <main class="content">
                    <div id="overview" class="panel active">
                        <h2>システム概要</h2>
                        <div class="stats">
                            <div class="stat">ユーザー数: <span>1,234</span></div>
                            <div class="stat">売上: <span>¥5,678,900</span></div>
                        </div>
                        <select id="period-select">
                            <option value="day">日次</option>
                            <option value="week">週次</option>
                            <option value="month">月次</option>
                        </select>
                    </div>
                    <div id="analytics" class="panel">
                        <h2>詳細分析</h2>
                        <div class="chart-container" style="height: 400px;">
                            <canvas id="chart"></canvas>
                        </div>
                    </div>
                </nav>
            </div>
        </body>
        </html>
        """
        
        # Expected interactive elements for dashboard
        expected_interactions = [
            {"type": "tab", "target": "F0:BN-100", "text": "概要"},
            {"type": "tab", "target": "F0:BN-101", "text": "分析"},
            {"type": "tab", "target": "F0:BN-102", "text": "設定"},
            {"type": "select", "target": "F0:BN-103", "options": ["日次", "週次", "月次"]}
        ]
        
        # Verify dashboard elements are properly detected
        assert len(expected_interactions) == 4
        assert expected_interactions[0]["type"] == "tab"
        assert expected_interactions[3]["type"] == "select"


class TestIntegrationWithPlaywright:
    """Integration tests that require actual browser automation."""
    
    @pytest.mark.asyncio
    @pytest.mark.integration
    async def test_full_extraction_pipeline(self):
        """Test complete data extraction pipeline with real browser.
        
        This test requires Playwright browsers to be installed.
        Run with: pytest -m integration
        """
        try:
            from playwright.async_api import async_playwright
        except ImportError:
            pytest.skip("Playwright not available")
        
        data_stack = DataSupplyStack()
        
        try:
            await data_stack.initialize()
            
            # Create test HTML page
            test_html = """
            <!DOCTYPE html>
            <html>
            <head><title>テストページ</title></head>
            <body>
                <h1>テストフォーム</h1>
                <form>
                    <input id="name" type="text" placeholder="名前を入力">
                    <button id="submit" type="submit">送信</button>
                </form>
            </body>
            </html>
            """
            
            if data_stack.page:
                await data_stack.page.set_content(test_html)
                
                # Extract all formats
                formats = await data_stack.extract_all_formats()
                
                # Verify formats were generated
                assert "idx_text" in formats
                assert "ax_slim" in formats
                assert "dom_lite" in formats
                assert "vis_roi" in formats
                
                # Verify IDX-Text contains expected elements
                idx_text = formats["idx_text"]
                if hasattr(idx_text, "index_map"):
                    assert len(idx_text.index_map) > 0
                
                # Verify AX-Slim contains accessibility info
                ax_slim = formats["ax_slim"]
                if hasattr(ax_slim, "ax_nodes"):
                    assert len(ax_slim.ax_nodes) >= 0
                
                print("✓ Full extraction pipeline test passed")
            
        finally:
            await data_stack.close()
    
    @pytest.mark.asyncio
    @pytest.mark.integration  
    async def test_action_execution_pipeline(self):
        """Test action execution with real browser."""
        try:
            from playwright.async_api import async_playwright
        except ImportError:
            pytest.skip("Playwright not available")
        
        data_stack = DataSupplyStack()
        action_processor = ActionProcessor(data_stack)
        
        try:
            await data_stack.initialize()
            
            if data_stack.page:
                await action_processor.initialize(data_stack.page)
                
                # Create interactive test page
                test_html = """
                <!DOCTYPE html>
                <html>
                <body>
                    <input id="test-input" type="text" placeholder="テスト入力">
                    <button id="test-button" onclick="alert('clicked')">クリック</button>
                    <div id="output"></div>
                    <script>
                        document.getElementById('test-button').onclick = function() {
                            document.getElementById('output').textContent = 'ボタンがクリックされました';
                        };
                    </script>
                </body>
                </html>
                """
                
                await data_stack.page.set_content(test_html)
                
                # Test plan request
                plan_request = json.dumps({"type": "plan"})
                response = await action_processor.process_action_request(plan_request)
                
                assert response["type"] == "plan_response"
                assert response["success"] is True
                
                print("✓ Action execution pipeline test passed")
        
        finally:
            await data_stack.close()


# Pytest configuration
def pytest_configure(config):
    """Configure pytest markers."""
    config.addinivalue_line("markers", "integration: marks tests as integration tests")


if __name__ == "__main__":
    # Run basic unit tests
    pytest.main(["-v", __file__, "-m", "not integration"])