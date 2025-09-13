#!/usr/bin/env python3
"""
Usage Example: Integration with existing web agent system

Shows how to integrate the Browser Operation Agent Data Supply Stack
with existing LLM-based web automation systems.
"""

import asyncio
import json
import logging
from pathlib import Path
import sys

# Add project root to path
sys.path.append(str(Path(__file__).resolve().parent))

from agent.browser.browser_agent import BrowserOperationAgent, BrowserAgentConfig


class WebAutomationExample:
    """Example integration with LLM-based web automation."""
    
    def __init__(self):
        self.config = BrowserAgentConfig(
            headless=True,
            staged_extraction=True,
            enable_readability=True,
            enable_ocr=True
        )
        self.agent = None
        
    async def initialize(self):
        """Initialize the browser agent."""
        self.agent = BrowserOperationAgent(self.config)
        await self.agent.initialize()
        
    async def navigate_and_extract(self, url: str):
        """Navigate to URL and extract page data for LLM."""
        if not self.agent:
            await self.initialize()
            
        # Navigate to page
        nav_result = await self.agent.navigate(url)
        if not nav_result["success"]:
            return {"error": f"Navigation failed: {nav_result['error']}"}
        
        # Get optimized content for LLM
        content = await self.agent.get_content_for_llm(content_type="mixed")
        if not content["success"]:
            return {"error": f"Content extraction failed: {content['error']}"}
        
        # Return structured data suitable for LLM prompt
        return {
            "url": url,
            "page_data": content["content"],
            "usage_instructions": content["content"]["usage_instructions"],
            "timestamp": content["timestamp"]
        }
    
    async def execute_llm_actions(self, llm_response: str):
        """Execute actions based on LLM response."""
        if not self.agent:
            raise RuntimeError("Agent not initialized")
        
        # Process LLM command
        result = await self.agent.process_llm_command(llm_response)
        
        # Return execution results
        return {
            "success": result.get("success", False),
            "actions_executed": len(result.get("results", [])),
            "execution_details": result.get("results", []),
            "metrics": result.get("session_metrics", {})
        }
    
    async def extract_article_content(self, url: str):
        """Extract article content using Readability."""
        if not self.agent:
            await self.initialize()
            
        # Navigate to article
        nav_result = await self.agent.navigate(url)
        if not nav_result["success"]:
            return {"error": f"Navigation failed: {nav_result['error']}"}
        
        # Extract article content
        article = await self.agent.extract_article_content(use_readability=True)
        
        return {
            "url": url,
            "title": article.get("title", ""),
            "content": article.get("text_content", ""),
            "word_count": len(article.get("text_content", "").split()),
            "extraction_method": article.get("extraction_method", ""),
            "success": article.get("success", False)
        }
    
    async def get_performance_metrics(self):
        """Get performance metrics for monitoring."""
        if not self.agent:
            return {"error": "Agent not initialized"}
            
        metrics = await self.agent.get_session_metrics()
        acceptance_report = await self.agent.create_acceptance_test_report()
        
        return {
            "session_metrics": metrics,
            "acceptance_test": acceptance_report["acceptance_test_results"],
            "overall_health": "good" if acceptance_report["acceptance_test_results"]["overall_pass"] else "needs_attention"
        }
    
    async def close(self):
        """Clean up resources."""
        if self.agent:
            await self.agent.close()


async def example_search_workflow():
    """Example: Search form interaction workflow."""
    print("=== Search Workflow Example ===")
    
    automation = WebAutomationExample()
    
    try:
        # Simulate search page (would be real URL in practice)
        search_html = """
        <!DOCTYPE html>
        <html>
        <head><title>商品検索</title></head>
        <body>
            <h1>商品検索</h1>
            <input id="search-input" type="text" placeholder="商品名を入力">
            <button id="search-btn">検索</button>
            <div id="results" style="display:none;">
                <h2>検索結果</h2>
                <div>ノートPC - ¥100,000</div>
            </div>
        </body>
        </html>
        """
        
        await automation.initialize()
        
        # Set content directly for demo
        if automation.agent and automation.agent.data_stack.page:
            await automation.agent.data_stack.page.set_content(search_html)
        
        # Extract page data
        page_data = await automation.agent.extract_page_data()
        print(f"✓ Page data extracted: {len(page_data['formats'])} formats")
        
        # Simulate LLM action command
        llm_command = json.dumps({
            "type": "act",
            "actions": [
                {"op": "type", "target": "F0:BN-123", "text": "ノートPC"},
                {"op": "click", "target": "F0:BN-124"}
            ]
        })
        
        # Execute actions
        action_result = await automation.execute_llm_actions(llm_command)
        print(f"✓ Actions processed: {action_result['actions_executed']} commands")
        
        # Get metrics
        metrics = await automation.get_performance_metrics()
        print(f"✓ Performance: {metrics['overall_health']}")
        
    finally:
        await automation.close()


async def example_article_extraction():
    """Example: Article content extraction."""
    print("\n=== Article Extraction Example ===")
    
    automation = WebAutomationExample()
    
    try:
        # Simulate news article
        article_html = """
        <!DOCTYPE html>
        <html>
        <head><title>AI技術の進歩</title></head>
        <body>
            <article>
                <h1>AI技術の最新動向</h1>
                <div class="author">記者: 田中太郎</div>
                <div class="content">
                    <p>AIの発展により、様々な分野で革新が起こっています。</p>
                    <p>特に自然言語処理の分野では目覚ましい進歩が見られます。</p>
                </div>
            </article>
        </body>
        </html>
        """
        
        await automation.initialize()
        
        # Set content for demo
        if automation.agent and automation.agent.data_stack.page:
            await automation.agent.data_stack.page.set_content(article_html)
        
        # Extract article
        article = await automation.agent.extract_article_content()
        print(f"✓ Article extracted: '{article.get('title', 'No title')}'")
        print(f"✓ Content length: {len(article.get('text_content', ''))} characters")
        
    finally:
        await automation.close()


async def example_integration_with_llm():
    """Example: Full integration with LLM system."""
    print("\n=== LLM Integration Example ===")
    
    automation = WebAutomationExample()
    
    try:
        await automation.initialize()
        
        # Simulate a dashboard page
        dashboard_html = """
        <!DOCTYPE html>
        <html>
        <head><title>管理ダッシュボード</title></head>
        <body>
            <nav>
                <button id="overview-tab" class="active">概要</button>
                <button id="users-tab">ユーザー</button>
                <button id="settings-tab">設定</button>
            </nav>
            <main>
                <h1>システム概要</h1>
                <div class="stats">
                    <div>ユーザー数: 1,234</div>
                    <div>売上: ¥5,678,900</div>
                </div>
                <select id="period">
                    <option value="day">日次</option>
                    <option value="week">週次</option>
                    <option value="month">月次</option>
                </select>
            </main>
        </body>
        </html>
        """
        
        if automation.agent and automation.agent.data_stack.page:
            await automation.agent.data_stack.page.set_content(dashboard_html)
        
        # Get content optimized for LLM
        llm_content = await automation.agent.get_content_for_llm()
        
        print("✓ LLM-optimized content prepared:")
        content = llm_content["content"]
        for format_name in content.keys():
            if format_name != "usage_instructions":
                print(f"   - {format_name}: available")
        
        # Simulate LLM understanding the page and generating commands
        simulated_llm_response = json.dumps({
            "type": "act",
            "actions": [
                {"op": "click", "target": "F0:BN-200"},  # Users tab
                {"op": "scroll", "direction": "down", "amount": 300}
            ]
        })
        
        # Execute LLM commands
        execution_result = await automation.execute_llm_actions(simulated_llm_response)
        print(f"✓ LLM commands executed: {execution_result['success']}")
        
        # Get final metrics
        final_metrics = await automation.get_performance_metrics()
        session_metrics = final_metrics["session_metrics"]
        print(f"✓ Session summary:")
        print(f"   - Extractions: {session_metrics['total_extractions']}")
        print(f"   - Actions: {session_metrics['total_actions']}")
        print(f"   - Success rate: {session_metrics.get('success_rate', 0):.1%}")
        
    finally:
        await automation.close()


def create_llm_prompt_template():
    """Example LLM prompt template for web automation."""
    return """
You are a web automation assistant. I will provide you with page data in 4 formats:

1. **IDX-Text**: Human-readable indexed elements
2. **AX-Slim**: Accessibility-focused interactive elements  
3. **DOM-Lite**: Structured element data with bounding boxes
4. **VIS-ROI**: Visual information with OCR text

**Available Actions:**
- click: Click an element using its stable reference ID
- type: Type text into an input field
- scroll: Scroll the page in a direction
- wait: Wait for a specified time

**Reference Format:**
Use stable IDs like "F0:BN-812345" to target elements precisely.

**Response Format:**
```json
{
  "type": "act",
  "actions": [
    {"op": "click", "target": "F0:BN-812345"},
    {"op": "type", "target": "F0:BN-812346", "text": "search query"}
  ]
}
```

**Page Data:**
{page_data}

**Task:**
{user_task}

Please analyze the page and provide the appropriate actions to complete the task.
"""


async def main():
    """Run all examples."""
    print("🌐 Browser Operation Agent - Usage Examples")
    print("=" * 50)
    
    try:
        await example_search_workflow()
        await example_article_extraction()
        await example_integration_with_llm()
        
        print("\n=== LLM Prompt Template ===")
        template = create_llm_prompt_template()
        print("Example prompt template created ✓")
        print(f"Template length: {len(template)} characters")
        
        print("\n🎯 Integration examples completed successfully!")
        print("\nNext steps:")
        print("1. Replace simulated HTML with real URLs")
        print("2. Connect to your LLM API")
        print("3. Implement error handling and retry logic")
        print("4. Add monitoring and logging")
        
    except Exception as e:
        print(f"\n❌ Example failed: {e}")
        logging.exception("Example execution failed")


if __name__ == "__main__":
    # Configure logging
    logging.basicConfig(level=logging.INFO)
    
    # Run examples
    asyncio.run(main())