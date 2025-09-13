"""
Integration layer for the data supply stack with the existing automation server.

Provides API endpoints for the 4 data formats and action execution.
"""

from __future__ import annotations

import logging
import json
from typing import Dict, Any, Optional
from dataclasses import asdict
import asyncio

from flask import jsonify, request
from playwright.async_api import Page

from agent.browser.data_supply import DataSupplyManager
from agent.actions.dsl_validator import DSLProcessor

log = logging.getLogger(__name__)


class DataSupplyIntegration:
    """Integration layer for data supply stack."""
    
    def __init__(self):
        self.data_supply_manager: Optional[DataSupplyManager] = None
        self.dsl_processor: Optional[DSLProcessor] = None
        self.initialized = False
    
    async def initialize_with_page(self, page: Page) -> None:
        """Initialize with Playwright page."""
        try:
            self.data_supply_manager = DataSupplyManager(page)
            await self.data_supply_manager.initialize()
            
            self.dsl_processor = DSLProcessor(self.data_supply_manager)
            
            self.initialized = True
            log.info("Data supply integration initialized successfully")
            
        except Exception as e:
            log.error(f"Failed to initialize data supply integration: {e}")
            raise
    
    def is_ready(self) -> bool:
        """Check if integration is ready."""
        return self.initialized and self.data_supply_manager is not None
    
    async def get_idx_text(self, viewport_only: bool = True) -> Dict[str, Any]:
        """Get IDX-Text v1 format."""
        if not self.is_ready():
            return {"error": "Data supply not initialized"}
        
        try:
            result = await self.data_supply_manager.data_supply.extract_idx_text(viewport_only)
            return {
                "format": "IDX-Text v1",
                "data": {
                    "meta": result.meta,
                    "text": result.text,
                    "index_map": result.index_map
                },
                "success": True
            }
        except Exception as e:
            log.error(f"IDX-Text extraction failed: {e}")
            return {"error": str(e), "success": False}
    
    async def get_ax_slim(self) -> Dict[str, Any]:
        """Get AX-Slim v1 format."""
        if not self.is_ready():
            return {"error": "Data supply not initialized"}
        
        try:
            result = await self.data_supply_manager.data_supply.extract_ax_slim()
            return {
                "format": "AX-Slim v1",
                "data": {
                    "root_name": result.root_name,
                    "ax_nodes": [asdict(node) for node in result.ax_nodes]
                },
                "success": True
            }
        except Exception as e:
            log.error(f"AX-Slim extraction failed: {e}")
            return {"error": str(e), "success": False}
    
    async def get_dom_lite(self) -> Dict[str, Any]:
        """Get DOM-Lite v1 format."""
        if not self.is_ready():
            return {"error": "Data supply not initialized"}
        
        try:
            result = await self.data_supply_manager.data_supply.extract_dom_lite()
            return {
                "format": "DOM-Lite v1",
                "data": {
                    "ver": result.ver,
                    "frame": result.frame,
                    "nodes": [asdict(node) for node in result.nodes]
                },
                "success": True
            }
        except Exception as e:
            log.error(f"DOM-Lite extraction failed: {e}")
            return {"error": str(e), "success": False}
    
    async def get_vis_roi(self, clip_region: Optional[Dict] = None) -> Dict[str, Any]:
        """Get VIS-ROI v1 format."""
        if not self.is_ready():
            return {"error": "Data supply not initialized"}
        
        try:
            result = await self.data_supply_manager.data_supply.extract_vis_roi(clip_region)
            return {
                "format": "VIS-ROI v1",
                "data": {
                    "image": result.image,
                    "ocr": [asdict(ocr_result) for ocr_result in result.ocr]
                },
                "success": True
            }
        except Exception as e:
            log.error(f"VIS-ROI extraction failed: {e}")
            return {"error": str(e), "success": False}
    
    async def get_all_formats(self, include_screenshot: bool = False, include_content: bool = False) -> Dict[str, Any]:
        """Get all 4 data formats."""
        if not self.is_ready():
            return {"error": "Data supply not initialized"}
        
        try:
            results = await self.data_supply_manager.get_all_formats(
                include_screenshot=include_screenshot,
                include_content=include_content
            )
            
            # Convert dataclass objects to dictionaries
            formatted_results = {}
            
            if "idx_text" in results:
                idx_text = results["idx_text"]
                formatted_results["idx_text"] = {
                    "meta": idx_text.meta,
                    "text": idx_text.text,
                    "index_map": idx_text.index_map
                }
            
            if "ax_slim" in results:
                ax_slim = results["ax_slim"]
                formatted_results["ax_slim"] = {
                    "root_name": ax_slim.root_name,
                    "ax_nodes": [asdict(node) for node in ax_slim.ax_nodes]
                }
            
            if "dom_lite" in results:
                dom_lite = results["dom_lite"]
                formatted_results["dom_lite"] = {
                    "ver": dom_lite.ver,
                    "frame": dom_lite.frame,
                    "nodes": [asdict(node) for node in dom_lite.nodes]
                }
            
            if "vis_roi" in results:
                vis_roi = results["vis_roi"]
                formatted_results["vis_roi"] = {
                    "image": vis_roi.image,
                    "ocr": [asdict(ocr_result) for ocr_result in vis_roi.ocr]
                }
            
            if "extracted_content" in results:
                content = results["extracted_content"]
                formatted_results["extracted_content"] = asdict(content)
            
            if "metrics" in results:
                formatted_results["metrics"] = asdict(results["metrics"])
            
            return {
                "formats": ["IDX-Text v1", "AX-Slim v1", "DOM-Lite v1", "VIS-ROI v1"],
                "data": formatted_results,
                "success": True
            }
            
        except Exception as e:
            log.error(f"All formats extraction failed: {e}")
            return {"error": str(e), "success": False}
    
    async def process_action_dsl(self, request_data: Dict[str, Any]) -> Dict[str, Any]:
        """Process LLM action DSL request."""
        if not self.is_ready():
            return {"error": "Data supply not initialized"}
        
        try:
            result = await self.dsl_processor.process_request(request_data)
            return result
        except Exception as e:
            log.error(f"Action DSL processing failed: {e}")
            return {
                "type": "error",
                "message": f"Processing failed: {str(e)}"
            }
    
    async def validate_target(self, target_ref: str) -> Dict[str, Any]:
        """Validate target reference."""
        if not self.is_ready():
            return {"error": "Data supply not initialized"}
        
        try:
            is_valid, stable_ref = await self.data_supply_manager.validate_target(target_ref)
            return {
                "target": target_ref,
                "valid": is_valid,
                "stable_ref": stable_ref.to_string() if stable_ref else None,
                "success": True
            }
        except Exception as e:
            log.error(f"Target validation failed: {e}")
            return {"error": str(e), "success": False}
    
    def get_metrics(self) -> Dict[str, Any]:
        """Get current metrics."""
        if not self.is_ready():
            return {"error": "Data supply not initialized"}
        
        try:
            data_metrics = asdict(self.data_supply_manager.data_supply.metrics)
            
            if self.dsl_processor:
                execution_stats = self.dsl_processor.executor.execution_stats
                return {
                    "data_extraction": data_metrics,
                    "action_execution": execution_stats,
                    "success": True
                }
            else:
                return {
                    "data_extraction": data_metrics,
                    "success": True
                }
        except Exception as e:
            log.error(f"Metrics retrieval failed: {e}")
            return {"error": str(e), "success": False}


# Global integration instance
_integration = DataSupplyIntegration()


def setup_data_supply_routes(app, get_page_func):
    """Setup Flask routes for data supply API."""
    
    @app.route("/api/data-supply/initialize", methods=["POST"])
    def initialize_data_supply():
        """Initialize data supply with current page."""
        try:
            page = get_page_func()
            if not page:
                return jsonify({"error": "No active page available"}), 400
            
            # Run async initialization
            loop = asyncio.new_event_loop()
            asyncio.set_event_loop(loop)
            try:
                loop.run_until_complete(_integration.initialize_with_page(page))
                return jsonify({"message": "Data supply initialized", "success": True})
            finally:
                loop.close()
                
        except Exception as e:
            return jsonify({"error": str(e), "success": False}), 500
    
    @app.route("/api/data-supply/idx-text", methods=["GET"])
    def get_idx_text():
        """Get IDX-Text v1 format."""
        viewport_only = request.args.get("viewport_only", "true").lower() == "true"
        
        loop = asyncio.new_event_loop()
        asyncio.set_event_loop(loop)
        try:
            result = loop.run_until_complete(_integration.get_idx_text(viewport_only))
            return jsonify(result)
        finally:
            loop.close()
    
    @app.route("/api/data-supply/ax-slim", methods=["GET"])
    def get_ax_slim():
        """Get AX-Slim v1 format."""
        loop = asyncio.new_event_loop()
        asyncio.set_event_loop(loop)
        try:
            result = loop.run_until_complete(_integration.get_ax_slim())
            return jsonify(result)
        finally:
            loop.close()
    
    @app.route("/api/data-supply/dom-lite", methods=["GET"])
    def get_dom_lite():
        """Get DOM-Lite v1 format."""
        loop = asyncio.new_event_loop()
        asyncio.set_event_loop(loop)
        try:
            result = loop.run_until_complete(_integration.get_dom_lite())
            return jsonify(result)
        finally:
            loop.close()
    
    @app.route("/api/data-supply/vis-roi", methods=["GET", "POST"])
    def get_vis_roi():
        """Get VIS-ROI v1 format."""
        clip_region = None
        if request.method == "POST":
            data = request.get_json() or {}
            clip_region = data.get("clip_region")
        
        loop = asyncio.new_event_loop()
        asyncio.set_event_loop(loop)
        try:
            result = loop.run_until_complete(_integration.get_vis_roi(clip_region))
            return jsonify(result)
        finally:
            loop.close()
    
    @app.route("/api/data-supply/all-formats", methods=["GET"])
    def get_all_formats():
        """Get all 4 data formats."""
        include_screenshot = request.args.get("screenshot", "false").lower() == "true"
        include_content = request.args.get("content", "false").lower() == "true"
        
        loop = asyncio.new_event_loop()
        asyncio.set_event_loop(loop)
        try:
            result = loop.run_until_complete(_integration.get_all_formats(
                include_screenshot=include_screenshot,
                include_content=include_content
            ))
            return jsonify(result)
        finally:
            loop.close()
    
    @app.route("/api/data-supply/action-dsl", methods=["POST"])
    def process_action_dsl():
        """Process LLM action DSL."""
        try:
            request_data = request.get_json()
            if not request_data:
                return jsonify({"error": "No JSON data provided"}), 400
            
            loop = asyncio.new_event_loop()
            asyncio.set_event_loop(loop)
            try:
                result = loop.run_until_complete(_integration.process_action_dsl(request_data))
                return jsonify(result)
            finally:
                loop.close()
                
        except Exception as e:
            return jsonify({"error": str(e), "success": False}), 500
    
    @app.route("/api/data-supply/validate-target", methods=["POST"])
    def validate_target():
        """Validate target reference."""
        try:
            data = request.get_json()
            if not data or "target" not in data:
                return jsonify({"error": "Target reference required"}), 400
            
            target_ref = data["target"]
            
            loop = asyncio.new_event_loop()
            asyncio.set_event_loop(loop)
            try:
                result = loop.run_until_complete(_integration.validate_target(target_ref))
                return jsonify(result)
            finally:
                loop.close()
                
        except Exception as e:
            return jsonify({"error": str(e), "success": False}), 500
    
    @app.route("/api/data-supply/metrics", methods=["GET"])
    def get_metrics():
        """Get current metrics."""
        try:
            result = _integration.get_metrics()
            return jsonify(result)
        except Exception as e:
            return jsonify({"error": str(e), "success": False}), 500
    
    @app.route("/api/data-supply/status", methods=["GET"])
    def get_status():
        """Get data supply status."""
        return jsonify({
            "initialized": _integration.is_ready(),
            "available_formats": ["IDX-Text v1", "AX-Slim v1", "DOM-Lite v1", "VIS-ROI v1"],
            "supported_actions": ["click", "type", "scroll", "hover", "navigate", "wait", "screenshot"],
            "success": True
        })


def get_integration() -> DataSupplyIntegration:
    """Get the global integration instance."""
    return _integration