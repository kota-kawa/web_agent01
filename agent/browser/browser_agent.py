"""
Browser Operation Agent - Main Integration Module

Provides high-level interface for the complete data supply stack.
Integrates all 4 data formats, action processing, and optimization features.
"""

from __future__ import annotations

import asyncio
import json
import logging
from datetime import datetime
from dataclasses import dataclass, asdict
from typing import Dict, List, Optional, Any, Union
from pathlib import Path

from .data_supply_stack import DataSupplyStack, ExtractionMetrics
from .action_processor import ActionProcessor

logger = logging.getLogger(__name__)


@dataclass
class BrowserAgentConfig:
    """Configuration for browser agent."""
    debug_port: int = 9222
    headless: bool = True
    viewport_width: int = 1400
    viewport_height: int = 900
    staged_extraction: bool = True
    enable_ocr: bool = True
    enable_readability: bool = True
    max_retry_attempts: int = 3
    action_timeout: int = 30000
    extraction_timeout: int = 10000


@dataclass
class SessionMetrics:
    """Comprehensive session metrics."""
    total_extractions: int = 0
    total_actions: int = 0
    successful_actions: int = 0
    failed_actions: int = 0
    avg_extraction_time_ms: float = 0.0
    avg_action_time_ms: float = 0.0
    total_tokens_sent: int = 0
    total_diff_bytes: int = 0
    retry_rate: float = 0.0
    not_found_rate: float = 0.0
    session_start_time: datetime = None
    last_activity_time: datetime = None


class BrowserOperationAgent:
    """Main browser operation agent class."""
    
    def __init__(self, config: BrowserAgentConfig = None):
        self.config = config or BrowserAgentConfig()
        self.data_stack = DataSupplyStack(debug_port=self.config.debug_port)
        self.action_processor = ActionProcessor(self.data_stack)
        self.session_metrics = SessionMetrics()
        self.is_initialized = False
        self.current_url: Optional[str] = None
        self.last_extraction: Optional[Dict[str, Any]] = None
        
    async def initialize(self) -> None:
        """Initialize the browser and all components."""
        if self.is_initialized:
            return
        
        try:
            logger.info("Initializing Browser Operation Agent...")
            
            # Initialize data stack (browser + CDP)
            await self.data_stack.initialize()
            
            # Initialize action processor
            if self.data_stack.page:
                await self.action_processor.initialize(self.data_stack.page)
                
                # Set viewport
                await self.data_stack.page.set_viewport_size({
                    "width": self.config.viewport_width,
                    "height": self.config.viewport_height
                })
            
            self.session_metrics.session_start_time = datetime.utcnow()
            self.session_metrics.last_activity_time = datetime.utcnow()
            self.is_initialized = True
            
            logger.info("Browser Operation Agent initialized successfully")
            
        except Exception as e:
            logger.error(f"Failed to initialize Browser Operation Agent: {e}")
            raise
    
    async def navigate(self, url: str) -> Dict[str, Any]:
        """Navigate to URL and perform initial extraction."""
        if not self.is_initialized:
            await self.initialize()
        
        try:
            logger.info(f"Navigating to: {url}")
            
            await self.data_stack.navigate(url)
            self.current_url = url
            self.session_metrics.last_activity_time = datetime.utcnow()
            
            # Perform initial extraction
            extraction_result = await self.extract_page_data(staged=self.config.staged_extraction)
            
            return {
                "success": True,
                "url": url,
                "extraction": extraction_result,
                "message": f"Successfully navigated to {url}"
            }
            
        except Exception as e:
            logger.error(f"Navigation failed: {e}")
            return {
                "success": False,
                "url": url,
                "error": str(e),
                "message": f"Failed to navigate to {url}"
            }
    
    async def extract_page_data(self, staged: bool = True, formats: List[str] = None) -> Dict[str, Any]:
        """Extract page data in all or specified formats."""
        if not self.is_initialized:
            raise RuntimeError("Agent not initialized")
        
        formats = formats or ["idx_text", "ax_slim", "dom_lite", "vis_roi"]
        
        try:
            start_time = datetime.utcnow()
            
            # Extract all formats
            extraction_data = await self.data_stack.extract_all_formats(staged=staged)
            
            # Calculate metrics
            end_time = datetime.utcnow()
            extraction_time = (end_time - start_time).total_seconds() * 1000
            
            self.session_metrics.total_extractions += 1
            self.session_metrics.avg_extraction_time_ms = (
                (self.session_metrics.avg_extraction_time_ms * (self.session_metrics.total_extractions - 1) + 
                 extraction_time) / self.session_metrics.total_extractions
            )
            
            # Update token metrics
            if "metrics" in extraction_data:
                metrics = extraction_data["metrics"]
                if hasattr(metrics, "tokens_estimated"):
                    self.session_metrics.total_tokens_sent += metrics.tokens_estimated
                if hasattr(metrics, "diff_bytes"):
                    self.session_metrics.total_diff_bytes += metrics.diff_bytes
            
            # Filter requested formats
            filtered_data = {}
            for fmt in formats:
                if fmt in extraction_data:
                    filtered_data[fmt] = extraction_data[fmt]
            
            # Store for diff calculations
            self.last_extraction = filtered_data
            self.session_metrics.last_activity_time = datetime.utcnow()
            
            return {
                "success": True,
                "formats": filtered_data,
                "extraction_time_ms": extraction_time,
                "url": self.current_url,
                "timestamp": end_time.isoformat()
            }
            
        except Exception as e:
            logger.error(f"Page extraction failed: {e}")
            return {
                "success": False,
                "error": str(e),
                "url": self.current_url,
                "timestamp": datetime.utcnow().isoformat()
            }
    
    async def process_llm_command(self, command_json: str) -> Dict[str, Any]:
        """Process LLM command and execute actions."""
        if not self.is_initialized:
            raise RuntimeError("Agent not initialized")
        
        try:
            start_time = datetime.utcnow()
            
            # Process action request
            result = await self.action_processor.process_action_request(command_json)
            
            # Update metrics
            end_time = datetime.utcnow()
            action_time = (end_time - start_time).total_seconds() * 1000
            
            self.session_metrics.total_actions += 1
            self.session_metrics.avg_action_time_ms = (
                (self.session_metrics.avg_action_time_ms * (self.session_metrics.total_actions - 1) + 
                 action_time) / self.session_metrics.total_actions
            )
            
            if result.get("success", False):
                self.session_metrics.successful_actions += 1
            else:
                self.session_metrics.failed_actions += 1
            
            # Update rates
            self.session_metrics.retry_rate = (
                self.session_metrics.failed_actions / self.session_metrics.total_actions
                if self.session_metrics.total_actions > 0 else 0
            )
            
            self.session_metrics.last_activity_time = datetime.utcnow()
            
            # Add execution metadata
            result["execution_time_ms"] = action_time
            result["session_metrics"] = asdict(self.session_metrics)
            
            return result
            
        except Exception as e:
            logger.error(f"LLM command processing failed: {e}")
            self.session_metrics.failed_actions += 1
            
            return {
                "success": False,
                "error": str(e),
                "type": "processing_error",
                "timestamp": datetime.utcnow().isoformat()
            }
    
    async def get_content_for_llm(self, content_type: str = "mixed", max_tokens: int = 4000) -> Dict[str, Any]:
        """Get optimized content for LLM consumption."""
        if not self.last_extraction:
            # Extract if we don't have recent data
            extraction_result = await self.extract_page_data()
            if not extraction_result["success"]:
                return extraction_result
        
        try:
            optimized_content = {}
            
            if content_type in ["text", "mixed"]:
                # IDX-Text format for human-readable references
                idx_text = self.last_extraction.get("idx_text")
                if idx_text:
                    optimized_content["indexed_text"] = {
                        "format": "idx_text_v1",
                        "data": asdict(idx_text) if hasattr(idx_text, "__dict__") else idx_text
                    }
            
            if content_type in ["accessibility", "mixed"]:
                # AX-Slim for accessibility-focused interaction
                ax_slim = self.last_extraction.get("ax_slim")
                if ax_slim:
                    optimized_content["accessibility"] = {
                        "format": "ax_slim_v1",
                        "data": asdict(ax_slim) if hasattr(ax_slim, "__dict__") else ax_slim
                    }
            
            if content_type in ["structure", "mixed"]:
                # DOM-Lite for structured analysis
                dom_lite = self.last_extraction.get("dom_lite")
                if dom_lite:
                    optimized_content["structure"] = {
                        "format": "dom_lite_v1",
                        "data": asdict(dom_lite) if hasattr(dom_lite, "__dict__") else dom_lite
                    }
            
            if content_type in ["visual", "mixed"]:
                # VIS-ROI for visual understanding
                vis_roi = self.last_extraction.get("vis_roi")
                if vis_roi:
                    optimized_content["visual"] = {
                        "format": "vis_roi_v1",
                        "data": asdict(vis_roi) if hasattr(vis_roi, "__dict__") else vis_roi
                    }
            
            # Add usage instructions for LLM
            optimized_content["usage_instructions"] = {
                "reference_format": "Use references like 'F0:BN-812345' for stable element targeting",
                "action_format": {
                    "click": {"op": "click", "target": "F0:BN-812345"},
                    "type": {"op": "type", "target": "F0:BN-812345", "text": "input text"},
                    "scroll": {"op": "scroll", "direction": "down", "amount": 800}
                },
                "response_format": {
                    "type": "act",
                    "actions": ["array of action objects"]
                }
            }
            
            return {
                "success": True,
                "content": optimized_content,
                "content_type": content_type,
                "max_tokens": max_tokens,
                "url": self.current_url,
                "timestamp": datetime.utcnow().isoformat()
            }
            
        except Exception as e:
            logger.error(f"Content optimization failed: {e}")
            return {
                "success": False,
                "error": str(e),
                "content_type": content_type
            }
    
    async def extract_article_content(self, use_readability: bool = True) -> Dict[str, Any]:
        """Extract article content using Readability."""
        if not self.data_stack.page:
            return {"success": False, "error": "Page not available"}
        
        try:
            if use_readability and self.config.enable_readability:
                # Get page HTML
                html = await self.data_stack.page.content()
                
                # Use Readability to extract main content
                from readabilipy import simple_json_from_html_string
                
                content = simple_json_from_html_string(html, use_readability=True)
                
                return {
                    "success": True,
                    "title": content.get("title", ""),
                    "byline": content.get("byline", ""),
                    "content": content.get("content", ""),
                    "text_content": content.get("text_content", ""),
                    "excerpt": content.get("excerpt", ""),
                    "length": content.get("length", 0),
                    "url": self.current_url,
                    "extraction_method": "readability"
                }
            else:
                # Fallback to basic text extraction
                text = await self.data_stack.page.inner_text("body")
                title = await self.data_stack.page.title()
                
                return {
                    "success": True,
                    "title": title,
                    "text_content": text,
                    "length": len(text),
                    "url": self.current_url,
                    "extraction_method": "basic"
                }
                
        except Exception as e:
            logger.error(f"Article extraction failed: {e}")
            return {
                "success": False,
                "error": str(e),
                "url": self.current_url
            }
    
    async def get_session_metrics(self) -> Dict[str, Any]:
        """Get comprehensive session metrics."""
        if self.session_metrics.session_start_time:
            session_duration = (
                datetime.utcnow() - self.session_metrics.session_start_time
            ).total_seconds()
        else:
            session_duration = 0
        
        return {
            "session_duration_seconds": session_duration,
            "total_extractions": self.session_metrics.total_extractions,
            "total_actions": self.session_metrics.total_actions,
            "successful_actions": self.session_metrics.successful_actions,
            "failed_actions": self.session_metrics.failed_actions,
            "success_rate": (
                self.session_metrics.successful_actions / self.session_metrics.total_actions
                if self.session_metrics.total_actions > 0 else 0
            ),
            "avg_extraction_time_ms": self.session_metrics.avg_extraction_time_ms,
            "avg_action_time_ms": self.session_metrics.avg_action_time_ms,
            "total_tokens_sent": self.session_metrics.total_tokens_sent,
            "total_diff_bytes": self.session_metrics.total_diff_bytes,
            "retry_rate": self.session_metrics.retry_rate,
            "not_found_rate": self.session_metrics.not_found_rate,
            "current_url": self.current_url,
            "last_activity": self.session_metrics.last_activity_time.isoformat() if self.session_metrics.last_activity_time else None
        }
    
    async def create_acceptance_test_report(self) -> Dict[str, Any]:
        """Create acceptance test report based on requirements."""
        metrics = await self.get_session_metrics()
        
        # Check acceptance criteria
        acceptance_criteria = {
            "retry_rate_below_1_percent": metrics["retry_rate"] <= 0.01,
            "not_found_rate_below_2_percent": metrics["not_found_rate"] <= 0.02,
            "success_rate_above_90_percent": metrics["success_rate"] >= 0.90,
            "avg_extraction_time_reasonable": metrics["avg_extraction_time_ms"] <= 5000,
            "avg_action_time_reasonable": metrics["avg_action_time_ms"] <= 2000
        }
        
        passed_criteria = sum(acceptance_criteria.values())
        total_criteria = len(acceptance_criteria)
        
        return {
            "acceptance_test_results": {
                "passed_criteria": passed_criteria,
                "total_criteria": total_criteria,
                "pass_rate": passed_criteria / total_criteria,
                "overall_pass": passed_criteria == total_criteria,
                "criteria_details": acceptance_criteria
            },
            "session_metrics": metrics,
            "timestamp": datetime.utcnow().isoformat()
        }
    
    async def close(self) -> None:
        """Clean up all resources."""
        try:
            if self.data_stack:
                await self.data_stack.close()
            
            logger.info("Browser Operation Agent closed successfully")
            
        except Exception as e:
            logger.error(f"Error during cleanup: {e}")


# Convenience functions for common use cases
async def create_search_form_agent(config: BrowserAgentConfig = None) -> BrowserOperationAgent:
    """Create agent optimized for search form interactions."""
    config = config or BrowserAgentConfig()
    config.staged_extraction = True  # Focus on interactive elements first
    config.enable_readability = False  # Not needed for forms
    
    agent = BrowserOperationAgent(config)
    await agent.initialize()
    return agent


async def create_article_reading_agent(config: BrowserAgentConfig = None) -> BrowserOperationAgent:
    """Create agent optimized for article reading."""
    config = config or BrowserAgentConfig()
    config.staged_extraction = False  # Extract all content
    config.enable_readability = True  # Enable content extraction
    config.enable_ocr = False  # Not typically needed for articles
    
    agent = BrowserOperationAgent(config)
    await agent.initialize()
    return agent


async def create_dashboard_agent(config: BrowserAgentConfig = None) -> BrowserOperationAgent:
    """Create agent optimized for dashboard interactions."""
    config = config or BrowserAgentConfig()
    config.staged_extraction = True  # Focus on controls
    config.enable_readability = False  # Not needed for dashboards
    config.enable_ocr = True  # May need for chart reading
    
    agent = BrowserOperationAgent(config)
    await agent.initialize()
    return agent