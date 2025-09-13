"""
Comprehensive tests for the browser automation data supply stack.

Tests all 4 data formats, action DSL validation, and E2E scenarios.
"""

import sys
from pathlib import Path
import asyncio
import json
import logging
from typing import Dict, Any

sys.path.append(str(Path(__file__).resolve().parents[1]))

from playwright.async_api import async_playwright
from agent.browser.data_supply import DataSupplyManager, StableNodeRef
from agent.actions.dsl_validator import DSLProcessor
from agent.utils.ocr import MockOCRProcessor
from agent.utils.content_extractor import MockContentExtractor

logging.basicConfig(level=logging.INFO)
log = logging.getLogger(__name__)


class TestDataSupplyStack:
    """Test suite for data supply stack."""
    
    def __init__(self):
        self.browser = None
        self.page = None
        self.data_supply_manager = None
        self.dsl_processor = None
    
    async def setup(self):
        """Setup test environment."""
        self.playwright = await async_playwright().start()
        self.browser = await self.playwright.chromium.launch(headless=True)
        self.page = await self.browser.new_page()
        
        # Initialize managers
        self.data_supply_manager = DataSupplyManager(self.page)
        await self.data_supply_manager.initialize()
        
        self.dsl_processor = DSLProcessor(self.data_supply_manager)
    
    async def teardown(self):
        """Cleanup test environment."""
        if self.browser:
            await self.browser.close()
        if self.playwright:
            await self.playwright.stop()
    
    async def test_search_form_scenario(self) -> Dict[str, Any]:
        """Test search form interaction scenario."""
        log.info("Testing search form scenario...")
        
        # Create a search form page
        search_html = """
        <!DOCTYPE html>
        <html>
        <head>
            <title>商品検索 - Test Site</title>
        </head>
        <body>
            <div id="main" class="container" aria-label="検索フォーム">
                <h1>商品検索</h1>
                <form id="search-form">
                    <input id="query" type="text" placeholder="キーワードを入力" value="" role="textbox">
                    <button id="search-btn" type="submit" role="button">検索</button>
                </form>
                <a href="/cart" role="link">カートを見る</a>
                <div id="results" style="display:none;">
                    <h2>検索結果</h2>
                    <div class="result-item">ノートPC - ¥80,000</div>
                    <div class="result-item">デスクトップPC - ¥120,000</div>
                </div>
            </div>
            <script>
                document.getElementById('search-form').addEventListener('submit', function(e) {
                    e.preventDefault();
                    document.getElementById('results').style.display = 'block';
                });
            </script>
        </body>
        </html>
        """
        
        await self.page.set_content(search_html)
        
        # Test data extraction
        results = {}
        
        # Test IDX-Text format
        idx_text = await self.data_supply_manager.data_supply.extract_idx_text()
        results["idx_text"] = {
            "has_meta": bool(idx_text.meta),
            "has_text": bool(idx_text.text),
            "has_index_map": bool(idx_text.index_map),
            "interactive_elements": len(idx_text.index_map)
        }
        
        # Test AX-Slim format
        ax_slim = await self.data_supply_manager.data_supply.extract_ax_slim()
        results["ax_slim"] = {
            "root_name": ax_slim.root_name,
            "node_count": len(ax_slim.ax_nodes),
            "has_interactive_nodes": any(node.visible for node in ax_slim.ax_nodes)
        }
        
        # Test DOM-Lite format
        dom_lite = await self.data_supply_manager.data_supply.extract_dom_lite()
        results["dom_lite"] = {
            "version": dom_lite.ver,
            "frame": dom_lite.frame,
            "node_count": len(dom_lite.nodes),
            "clickable_elements": len([n for n in dom_lite.nodes if n.clickable])
        }
        
        # Test VIS-ROI format (with mock OCR)
        vis_roi = await self.data_supply_manager.data_supply.extract_vis_roi()
        results["vis_roi"] = {
            "has_image": bool(vis_roi.image),
            "image_format": vis_roi.image.get("format"),
            "ocr_results": len(vis_roi.ocr)
        }
        
        # Test action execution
        search_action = {
            "type": "act",
            "actions": [
                {"op": "type", "target": "F0:CSS-#query", "text": "ノートPC"},
                {"op": "click", "target": "F0:CSS-#search-btn"}
            ]
        }
        
        action_result = await self.dsl_processor.process_request(search_action)
        results["action_execution"] = {
            "success": action_result.get("success", False),
            "type": action_result.get("type")
        }
        
        # Verify results are visible
        await asyncio.sleep(0.5)  # Wait for JS to run
        results_div = await self.page.query_selector("#results")
        is_visible = await results_div.is_visible() if results_div else False
        results["search_completed"] = is_visible
        
        return results
    
    async def test_news_article_scenario(self) -> Dict[str, Any]:
        """Test news article content extraction scenario."""
        log.info("Testing news article scenario...")
        
        # Create a news article page
        news_html = """
        <!DOCTYPE html>
        <html>
        <head>
            <title>AI技術の最新動向 - Tech News</title>
            <meta name="author" content="田中太郎">
            <meta name="description" content="AI技術の最新動向について詳しく解説します。">
            <meta property="article:published_time" content="2024-01-15T10:30:00Z">
        </head>
        <body>
            <header>
                <nav>ナビゲーション</nav>
            </header>
            <main>
                <article>
                    <h1>AI技術の最新動向</h1>
                    <div class="byline">
                        <span class="author">田中太郎</span>
                        <time datetime="2024-01-15T10:30:00Z">2024年1月15日</time>
                    </div>
                    <div class="content">
                        <p>人工知能（AI）技術は急速に発展しており、様々な分野で革新的な変化をもたらしています。</p>
                        <p>特に大規模言語モデル（LLM）の進歩は目覚ましく、自然言語処理の能力が大幅に向上しました。</p>
                        <p>本記事では、AI技術の最新動向と今後の展望について詳しく解説します。</p>
                        <h2>機械学習の進歩</h2>
                        <p>機械学習アルゴリズムの改良により、より精度の高い予測が可能になっています。</p>
                        <h2>応用分野の拡大</h2>
                        <p>AIは医療、金融、製造業など、あらゆる分野で活用されています。</p>
                    </div>
                </article>
            </main>
            <aside>サイドバー</aside>
            <footer>フッター</footer>
        </body>
        </html>
        """
        
        await self.page.set_content(news_html)
        
        results = {}
        
        # Test content extraction
        extracted_content = await self.data_supply_manager.data_supply.content_extractor.extract_content(self.page)
        results["content_extraction"] = {
            "success": extracted_content.success,
            "has_title": bool(extracted_content.title),
            "has_content": bool(extracted_content.content),
            "has_author": bool(extracted_content.author),
            "has_date": bool(extracted_content.date),
            "word_count": extracted_content.word_count
        }
        
        # Test article detection
        is_article = await self.data_supply_manager.data_supply.content_extractor.is_article_page(self.page)
        results["article_detection"] = is_article
        
        # Test all formats with content
        all_formats = await self.data_supply_manager.get_all_formats(
            include_screenshot=True, 
            include_content=True
        )
        results["all_formats"] = {
            "has_idx_text": "idx_text" in all_formats,
            "has_ax_slim": "ax_slim" in all_formats,
            "has_dom_lite": "dom_lite" in all_formats,
            "has_vis_roi": "vis_roi" in all_formats,
            "has_extracted_content": "extracted_content" in all_formats
        }
        
        return results
    
    async def test_dashboard_scenario(self) -> Dict[str, Any]:
        """Test dashboard with tabs and dropdowns scenario."""
        log.info("Testing dashboard scenario...")
        
        # Create a dashboard page
        dashboard_html = """
        <!DOCTYPE html>
        <html>
        <head>
            <title>管理ダッシュボード - Admin</title>
        </head>
        <body>
            <div class="dashboard">
                <nav class="tabs">
                    <button id="tab-1" class="tab active" data-tab="overview">概要</button>
                    <button id="tab-2" class="tab" data-tab="users">ユーザー</button>
                    <button id="tab-3" class="tab" data-tab="settings">設定</button>
                </nav>
                
                <div class="tab-content">
                    <div id="overview" class="tab-panel active">
                        <h2>概要</h2>
                        <div class="stats">
                            <div class="stat">ユーザー数: 1,234</div>
                            <div class="stat">売上: ¥5,678,900</div>
                        </div>
                        <select id="period-select">
                            <option value="week">週間</option>
                            <option value="month">月間</option>
                            <option value="year">年間</option>
                        </select>
                    </div>
                    
                    <div id="users" class="tab-panel" style="display:none;">
                        <h2>ユーザー管理</h2>
                        <button id="add-user">ユーザー追加</button>
                        <div class="user-list">
                            <div class="user-item">ユーザー1</div>
                            <div class="user-item">ユーザー2</div>
                        </div>
                    </div>
                    
                    <div id="settings" class="tab-panel" style="display:none;">
                        <h2>設定</h2>
                        <form>
                            <input type="text" id="site-name" placeholder="サイト名">
                            <button type="submit">保存</button>
                        </form>
                    </div>
                </div>
            </div>
            
            <script>
                document.querySelectorAll('.tab').forEach(tab => {
                    tab.addEventListener('click', function() {
                        // Remove active class from all tabs and panels
                        document.querySelectorAll('.tab').forEach(t => t.classList.remove('active'));
                        document.querySelectorAll('.tab-panel').forEach(p => p.style.display = 'none');
                        
                        // Add active class to clicked tab
                        this.classList.add('active');
                        
                        // Show corresponding panel
                        const tabId = this.getAttribute('data-tab');
                        document.getElementById(tabId).style.display = 'block';
                    });
                });
                
                document.getElementById('period-select').addEventListener('change', function() {
                    console.log('Period changed to:', this.value);
                });
            </script>
        </body>
        </html>
        """
        
        await self.page.set_content(dashboard_html)
        
        results = {}
        
        # Test initial state
        initial_data = await self.data_supply_manager.get_all_formats()
        results["initial_extraction"] = {
            "interactive_elements": len(initial_data["idx_text"].index_map),
            "clickable_nodes": len([n for n in initial_data["dom_lite"].nodes if n.clickable])
        }
        
        # Test tab switching action
        tab_switch_action = {
            "type": "act",
            "actions": [
                {"op": "click", "target": "F0:CSS-#tab-2"}
            ]
        }
        
        tab_result = await self.dsl_processor.process_request(tab_switch_action)
        results["tab_switch"] = {
            "success": tab_result.get("success", False)
        }
        
        # Wait for tab switch to complete
        await asyncio.sleep(0.5)
        
        # Test dropdown selection
        dropdown_action = {
            "type": "act", 
            "actions": [
                {"op": "click", "target": "F0:CSS-#tab-1"},  # Switch back to overview
                {"op": "wait", "duration": 300},
                {"op": "click", "target": "F0:CSS-#period-select"}
            ]
        }
        
        dropdown_result = await self.dsl_processor.process_request(dropdown_action)
        results["dropdown_interaction"] = {
            "success": dropdown_result.get("success", False)
        }
        
        # Test scroll behavior (simulate long content)
        scroll_action = {
            "type": "act",
            "actions": [
                {"op": "scroll", "direction": "down", "amount": 500},
                {"op": "scroll", "direction": "up", "amount": 250}
            ]
        }
        
        scroll_result = await self.dsl_processor.process_request(scroll_action)
        results["scroll_behavior"] = {
            "success": scroll_result.get("success", False)
        }
        
        return results
    
    async def test_stable_references(self) -> Dict[str, Any]:
        """Test stable reference system."""
        log.info("Testing stable reference system...")
        
        html_content = """
        <!DOCTYPE html>
        <html>
        <body>
            <button id="test-btn" data-test="button">Test Button</button>
            <input id="test-input" type="text" placeholder="Test Input">
        </body>
        </html>
        """
        
        await self.page.set_content(html_content)
        
        results = {}
        
        # Test stable reference creation and parsing
        test_refs = [
            "F0:BN-12345",
            "F0:AX-67890", 
            "F0:CSS-#test-btn"
        ]
        
        parsed_refs = []
        for ref_str in test_refs:
            parsed = StableNodeRef.from_string(ref_str)
            if parsed:
                back_to_str = parsed.to_string()
                parsed_refs.append({
                    "original": ref_str,
                    "parsed": True,
                    "reconstructed": back_to_str,
                    "matches": ref_str == back_to_str
                })
            else:
                parsed_refs.append({
                    "original": ref_str,
                    "parsed": False
                })
        
        results["reference_parsing"] = parsed_refs
        
        # Test target validation
        validation_tests = [
            "F0:CSS-#test-btn",
            "F0:CSS-#test-input",
            "F0:CSS-#nonexistent"
        ]
        
        validation_results = []
        for target in validation_tests:
            is_valid, stable_ref = await self.data_supply_manager.validate_target(target)
            validation_results.append({
                "target": target,
                "valid": is_valid,
                "has_stable_ref": stable_ref is not None
            })
        
        results["target_validation"] = validation_results
        
        return results
    
    async def test_differential_updates(self) -> Dict[str, Any]:
        """Test differential update system."""
        log.info("Testing differential updates...")
        
        # Initial page
        initial_html = """
        <!DOCTYPE html>
        <html>
        <body>
            <div id="content">
                <p>Initial content</p>
                <button id="load-more">Load More</button>
            </div>
        </body>
        </html>
        """
        
        await self.page.set_content(initial_html)
        
        # Get initial snapshot
        initial_snapshot = await self.data_supply_manager.get_all_formats()
        
        # Simulate content change
        await self.page.evaluate("""
            () => {
                const content = document.getElementById('content');
                const newPara = document.createElement('p');
                newPara.textContent = 'New dynamic content';
                content.insertBefore(newPara, document.getElementById('load-more'));
            }
        """)
        
        # Get updated snapshot
        updated_snapshot = await self.data_supply_manager.get_all_formats()
        
        # Test change detection
        changes = await self.data_supply_manager.data_supply.detect_changes(updated_snapshot)
        
        results = {
            "initial_nodes": len(initial_snapshot["dom_lite"].nodes),
            "updated_nodes": len(updated_snapshot["dom_lite"].nodes),
            "change_type": changes.get("type"),
            "has_changes": changes.get("type") != "minor"
        }
        
        return results
    
    async def test_error_handling(self) -> Dict[str, Any]:
        """Test error handling and recovery."""
        log.info("Testing error handling...")
        
        results = {}
        
        # Test invalid action DSL
        invalid_action = {
            "type": "act",
            "actions": [
                {"op": "invalid_operation", "target": "nowhere"}
            ]
        }
        
        invalid_result = await self.dsl_processor.process_request(invalid_action)
        results["invalid_action"] = {
            "handled": invalid_result.get("type") == "retry",
            "has_errors": "errors" in invalid_result
        }
        
        # Test missing target
        missing_target_action = {
            "type": "act",
            "actions": [
                {"op": "click", "target": "F0:CSS-#nonexistent"}
            ]
        }
        
        missing_result = await self.dsl_processor.process_request(missing_target_action)
        results["missing_target"] = {
            "handled": not missing_result.get("success", True),
            "retry_suggested": missing_result.get("retry_suggested", False)
        }
        
        return results
    
    async def run_all_tests(self) -> Dict[str, Any]:
        """Run all test scenarios."""
        log.info("Starting comprehensive test suite...")
        
        await self.setup()
        
        try:
            test_results = {}
            
            # Run individual test scenarios
            test_results["search_form"] = await self.test_search_form_scenario()
            test_results["news_article"] = await self.test_news_article_scenario()
            test_results["dashboard"] = await self.test_dashboard_scenario()
            test_results["stable_references"] = await self.test_stable_references()
            test_results["differential_updates"] = await self.test_differential_updates()
            test_results["error_handling"] = await self.test_error_handling()
            
            # Overall success rate
            success_count = 0
            total_tests = 0
            
            for scenario, results in test_results.items():
                if isinstance(results, dict):
                    for key, value in results.items():
                        total_tests += 1
                        if isinstance(value, bool) and value:
                            success_count += 1
                        elif isinstance(value, dict) and value.get("success", False):
                            success_count += 1
            
            test_results["summary"] = {
                "total_tests": total_tests,
                "successful_tests": success_count,
                "success_rate": success_count / total_tests if total_tests > 0 else 0.0,
                "overall_success": success_count / total_tests >= 0.8 if total_tests > 0 else False
            }
            
            return test_results
            
        finally:
            await self.teardown()


async def main():
    """Run the test suite."""
    test_suite = TestDataSupplyStack()
    results = await test_suite.run_all_tests()
    
    # Print results
    print("\n" + "="*80)
    print("BROWSER AUTOMATION DATA SUPPLY STACK TEST RESULTS")
    print("="*80)
    
    for scenario, result in results.items():
        if scenario == "summary":
            continue
        print(f"\n{scenario.upper().replace('_', ' ')}:")
        print("-" * 40)
        if isinstance(result, dict):
            for key, value in result.items():
                status = "✓" if (isinstance(value, bool) and value) or (isinstance(value, dict) and value.get("success", False)) else "✗"
                print(f"  {status} {key}: {value}")
    
    # Print summary
    summary = results["summary"]
    print(f"\n{'='*80}")
    print("SUMMARY:")
    print(f"  Total Tests: {summary['total_tests']}")
    print(f"  Successful: {summary['successful_tests']}")
    print(f"  Success Rate: {summary['success_rate']:.2%}")
    print(f"  Overall Result: {'PASS' if summary['overall_success'] else 'FAIL'}")
    print(f"{'='*80}")
    
    # Save detailed results to file
    with open("/tmp/test_results.json", "w", encoding="utf-8") as f:
        json.dump(results, f, indent=2, ensure_ascii=False, default=str)
    
    print(f"\nDetailed results saved to: /tmp/test_results.json")
    
    return summary['overall_success']


if __name__ == "__main__":
    success = asyncio.run(main())
    sys.exit(0 if success else 1)