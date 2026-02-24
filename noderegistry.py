# -*- coding: utf-8 -*-
"""
Weave: A modular PySide6 framework for the visual synthesis 
and execution of high-concurrency simulation workflows.
Copyright (c) 2026 opticsWolf

SPDX-License-Identifier: Apache-2.0

noderegistry.py
------------------
A robust, Singleton-based registry for Node Graph discovery.
Uses the Decorator pattern for auto-registration at import time.
Includes powerful search capabilities for node discovery.
"""

import os
from typing import Dict, List, Type, Union, Optional, Any, TYPE_CHECKING, NamedTuple
from collections import defaultdict
import re

# Lazy import for QIcon — only needed when get_node_icon() is called,
# which keeps the registry importable in headless / test environments.
_QIcon = None

def _ensure_qicon():
    """Import QIcon on first use (avoids hard dependency on a running QApp)."""
    global _QIcon
    if _QIcon is None:
        from PySide6.QtGui import QIcon
        _QIcon = QIcon
    return _QIcon

# Use TYPE_CHECKING to avoid circular imports during runtime
if TYPE_CHECKING:
    from weave.basenode import ActiveNode, ManualNode

from weave.logger import get_logger
log = get_logger("Registry")

# Type alias for our node classes
NodeCls = Type[Union['ActiveNode', 'ManualNode']]

class SearchResult(NamedTuple):
    """
    Represents a single search result with relevance scoring.
    
    Attributes:
        node_cls: The matched node class.
        score: Relevance score (higher = better match).
        matched_fields: List of field names that matched the query.
        category: The node's category.
        subcategory: The node's subcategory (or None).
    """
    node_cls: NodeCls
    score: float
    matched_fields: List[str]
    category: str
    subcategory: Optional[str]


class NodeRegistry:
    """
    Central repository for all available node types.
    
    Structure:
        Category -> Subcategory (Optional[str]) -> List[NodeClass]
    """
    
    # Field weights for relevance scoring
    FIELD_WEIGHTS = {
        'name': 10.0,        # Exact/partial name match is most important
        'node_name': 10.0,   # Display name equally important
        'class': 5.0,        # Category match
        'subclass': 5.0,     # Subcategory match
        'description': 3.0,  # Description match
        'tags': 4.0,         # Tags match
    }
    
    def __init__(self) -> None:
        # Structure: { "Category": { "Subcategory": [NodeClass, ...], None: [DirectNode, ...] } }
        self._tree: Dict[str, Dict[Optional[str], List[NodeCls]]] = defaultdict(lambda: defaultdict(list))
        # Structure: { "NodeClassName": NodeClass }
        self._flat_map: Dict[str, NodeCls] = {}
        # Icon cache: { "icon_path_string": QIcon_instance }
        self._icon_cache: Dict[str, Any] = {}
        # Sentinel for paths that failed to load (avoid retrying)
        self._ICON_MISS = object()

    def register(self, cls: NodeCls) -> NodeCls:
        """
        Registers a class. Intended to be used as a decorator.
        Reads 'node_class' and 'node_subclass' from the class attributes.
        
        If 'node_subclass' is None or missing, the node is registered directly 
        under the category using 'None' as the key.
        """
        # 1. Extract Metadata (Safely)
        category: str = getattr(cls, "node_class", "Uncategorized")
        subcategory: Optional[str] = getattr(cls, "node_subclass", None)
        name: str = cls.__name__

        # 2. Duplicate Check
        if name in self._flat_map:
            log.warning(f"Warning: Overwriting node type '{name}'")

        # 3. Store in Flat Map (O(1))
        self._flat_map[name] = cls
        
        # 4. Store in Tree (O(1))
        target_list = self._tree[category][subcategory]
        if cls not in target_list:
            target_list.append(cls)
        
        return cls

    def get_tree(self) -> Dict[str, Dict[Optional[str], List[NodeCls]]]:
        """
        Returns the hierarchical structure for UI generation (Menus).
        """
        clean_tree: Dict[str, Dict[Optional[str], List[NodeCls]]] = {}

        for category, sub_dict in self._tree.items():
            if not sub_dict:
                continue

            clean_sub_dict: Dict[Optional[str], List[NodeCls]] = {}
            
            for subcategory, node_list in sub_dict.items():
                if node_list:
                    clean_sub_dict[subcategory] = node_list
            
            if clean_sub_dict:
                clean_tree[category] = clean_sub_dict

        return clean_tree

    def get_node_class(self, class_name: str) -> Optional[NodeCls]:
        """Lookup a class type by its name (string)."""
        return self._flat_map.get(class_name)

    def instantiate(self, class_name: str, *args: Any, **kwargs: Any) -> Optional[Any]:
        """Factory method to create a node instance from a string name."""
        cls = self.get_node_class(class_name)
        if cls:
            return cls(*args, **kwargs)
        
        log.error(f"Error: Node type '{class_name}' not found.")
        return None

    # ==========================================================================
    # SEARCH FUNCTIONALITY
    # ==========================================================================

    def search(
        self,
        query: str,
        *,
        search_name: bool = True,
        search_class: bool = True,
        search_subclass: bool = True,
        search_description: bool = True,
        search_tags: bool = True,
        case_sensitive: bool = False,
        min_score: float = 0.0,
        limit: Optional[int] = None,
        category_filter: Optional[str] = None,
        subcategory_filter: Optional[str] = None,
    ) -> List[SearchResult]:
        """
        Search for nodes matching the given query across multiple fields.
        
        Args:
            query: The search string. Supports multiple space-separated terms.
            search_name: Search in class name and node_name/NODE_NAME attributes.
            search_class: Search in node_class (category).
            search_subclass: Search in node_subclass (subcategory).
            search_description: Search in node_description/NODE_DESCRIPTION.
            search_tags: Search in node_tags/NODE_TAGS list.
            case_sensitive: Whether the search is case-sensitive.
            min_score: Minimum relevance score to include in results.
            limit: Maximum number of results to return (None = unlimited).
            category_filter: Only search within this category (exact match).
            subcategory_filter: Only search within this subcategory (exact match).
            
        Returns:
            List of SearchResult objects sorted by relevance score (descending).
            
        Example:
            >>> results = NODE_REGISTRY.search("math add")
            >>> for r in results:
            ...     print(f"{r.node_cls.__name__}: {r.score:.1f} ({r.matched_fields})")
        """
        if not query or not query.strip():
            return []
        
        # Normalize query
        query_normalized = query if case_sensitive else query.lower()
        terms = query_normalized.split()
        
        results: List[SearchResult] = []
        
        # Iterate through all registered nodes
        for category, sub_dict in self._tree.items():
            # Apply category filter if specified
            if category_filter is not None:
                filter_cat = category_filter if case_sensitive else category_filter.lower()
                check_cat = category if case_sensitive else category.lower()
                if check_cat != filter_cat:
                    continue
            
            for subcategory, node_list in sub_dict.items():
                # Apply subcategory filter if specified
                if subcategory_filter is not None:
                    if subcategory is None:
                        continue
                    filter_sub = subcategory_filter if case_sensitive else subcategory_filter.lower()
                    check_sub = subcategory if case_sensitive else subcategory.lower()
                    if check_sub != filter_sub:
                        continue
                
                for node_cls in node_list:
                    score, matched_fields = self._calculate_match_score(
                        node_cls,
                        terms,
                        category,
                        subcategory,
                        case_sensitive=case_sensitive,
                        search_name=search_name,
                        search_class=search_class,
                        search_subclass=search_subclass,
                        search_description=search_description,
                        search_tags=search_tags,
                    )
                    
                    if score > min_score and matched_fields:
                        results.append(SearchResult(
                            node_cls=node_cls,
                            score=score,
                            matched_fields=matched_fields,
                            category=category,
                            subcategory=subcategory,
                        ))
        
        # Sort by score (descending), then by name (ascending) for stable ordering
        results.sort(key=lambda r: (-r.score, r.node_cls.__name__))
        
        # Apply limit if specified
        if limit is not None and limit > 0:
            results = results[:limit]
        
        return results

    def _calculate_match_score(
        self,
        node_cls: NodeCls,
        terms: List[str],
        category: str,
        subcategory: Optional[str],
        *,
        case_sensitive: bool,
        search_name: bool,
        search_class: bool,
        search_subclass: bool,
        search_description: bool,
        search_tags: bool,
    ) -> tuple[float, List[str]]:
        """
        Calculate the relevance score for a node against search terms.
        
        Returns:
            Tuple of (score, list of matched field names).
        """
        total_score = 0.0
        matched_fields: List[str] = []
        
        def normalize(text: Optional[str]) -> str:
            if text is None:
                return ""
            return text if case_sensitive else text.lower()
        
        def check_field(field_value: str, field_name: str, weight: float) -> float:
            """Check if any term matches this field and return weighted score."""
            if not field_value:
                return 0.0
            
            field_score = 0.0
            normalized = normalize(field_value)
            
            for term in terms:
                if term in normalized:
                    # Exact match bonus
                    if normalized == term:
                        field_score += weight * 2.0
                    # Starts with bonus
                    elif normalized.startswith(term):
                        field_score += weight * 1.5
                    # Contains
                    else:
                        field_score += weight * 1.0
            
            if field_score > 0 and field_name not in matched_fields:
                matched_fields.append(field_name)
            
            return field_score
        
        # Search in name fields
        if search_name:
            # Class name (e.g., "AddNode")
            total_score += check_field(node_cls.__name__, 'name', self.FIELD_WEIGHTS['name'])
            
            # Display name (node_name or NODE_NAME attribute)
            display_name = getattr(node_cls, 'node_name', None) or getattr(node_cls, 'NODE_NAME', None)
            if display_name:
                total_score += check_field(str(display_name), 'node_name', self.FIELD_WEIGHTS['node_name'])
        
        # Search in category
        if search_class:
            total_score += check_field(category, 'class', self.FIELD_WEIGHTS['class'])
        
        # Search in subcategory
        if search_subclass and subcategory:
            total_score += check_field(subcategory, 'subclass', self.FIELD_WEIGHTS['subclass'])
        
        # Search in description
        if search_description:
            description = (
                getattr(node_cls, 'node_description', None) or 
                getattr(node_cls, 'NODE_DESCRIPTION', None) or
                getattr(node_cls, '__doc__', None)
            )
            if description:
                total_score += check_field(str(description), 'description', self.FIELD_WEIGHTS['description'])
        
        # Search in tags
        if search_tags:
            tags = getattr(node_cls, 'node_tags', None) or getattr(node_cls, 'NODE_TAGS', None)
            if tags:
                if isinstance(tags, (list, tuple)):
                    tags_str = " ".join(str(t) for t in tags)
                else:
                    tags_str = str(tags)
                total_score += check_field(tags_str, 'tags', self.FIELD_WEIGHTS['tags'])
        
        return total_score, matched_fields

    def search_by_category(self, category: str, case_sensitive: bool = False) -> List[NodeCls]:
        """
        Get all nodes in a specific category.
        
        Args:
            category: The category name to filter by.
            case_sensitive: Whether matching is case-sensitive.
            
        Returns:
            List of node classes in the specified category.
        """
        results: List[NodeCls] = []
        
        for cat, sub_dict in self._tree.items():
            cat_match = cat if case_sensitive else cat.lower()
            search_cat = category if case_sensitive else category.lower()
            
            if cat_match == search_cat:
                for node_list in sub_dict.values():
                    results.extend(node_list)
        
        return results

    def search_by_subcategory(
        self, 
        subcategory: str, 
        category: Optional[str] = None,
        case_sensitive: bool = False
    ) -> List[NodeCls]:
        """
        Get all nodes in a specific subcategory.
        
        Args:
            subcategory: The subcategory name to filter by.
            category: Optional category to also filter by.
            case_sensitive: Whether matching is case-sensitive.
            
        Returns:
            List of node classes in the specified subcategory.
        """
        results: List[NodeCls] = []
        
        for cat, sub_dict in self._tree.items():
            # Check category filter if provided
            if category is not None:
                cat_match = cat if case_sensitive else cat.lower()
                search_cat = category if case_sensitive else category.lower()
                if cat_match != search_cat:
                    continue
            
            for subcat, node_list in sub_dict.items():
                if subcat is None:
                    continue
                    
                subcat_match = subcat if case_sensitive else subcat.lower()
                search_subcat = subcategory if case_sensitive else subcategory.lower()
                
                if subcat_match == search_subcat:
                    results.extend(node_list)
        
        return results

    def get_all_categories(self) -> List[str]:
        """
        Get a list of all registered categories.
        
        Returns:
            Sorted list of category names.
        """
        return sorted(self._tree.keys())

    def get_all_subcategories(self, category: Optional[str] = None) -> List[str]:
        """
        Get a list of all registered subcategories.
        
        Args:
            category: If provided, only return subcategories within this category.
            
        Returns:
            Sorted list of subcategory names (excludes None).
        """
        subcategories: set[str] = set()
        
        for cat, sub_dict in self._tree.items():
            if category is not None and cat != category:
                continue
            
            for subcat in sub_dict.keys():
                if subcat is not None:
                    subcategories.add(subcat)
        
        return sorted(subcategories)

    def get_all_nodes(self) -> List[NodeCls]:
        """
        Get a flat list of all registered node classes.
        
        Returns:
            List of all registered node classes.
        """
        return list(self._flat_map.values())

    # ==========================================================================
    # NODE METADATA HELPERS
    # ==========================================================================

    @staticmethod
    def get_node_display_name(node_cls: NodeCls) -> str:
        """
        Resolve the best human-readable display name for a node class.

        Priority:
            1. ``node_name``  (ClassVar from BaseControlNode)
            2. ``NODE_NAME``  (legacy uppercase convention)
            3. ``__name__``   (fallback to Python class name)
        
        Args:
            node_cls: The node class to inspect.
            
        Returns:
            Display name string.
        """
        return (
            getattr(node_cls, 'node_name', None)
            or getattr(node_cls, 'NODE_NAME', None)
            or getattr(node_cls, '__name__', 'Unknown Node')
        )

    @staticmethod
    def get_node_description(node_cls: NodeCls) -> Optional[str]:
        """
        Resolve the description for a node class.

        Priority:
            1. ``node_description``  (ClassVar from BaseControlNode)
            2. ``NODE_DESCRIPTION``  (legacy uppercase convention)
            3. ``__doc__``           (class docstring, stripped)
        
        Args:
            node_cls: The node class to inspect.
            
        Returns:
            Description string, or None.
        """
        desc = (
            getattr(node_cls, 'node_description', None)
            or getattr(node_cls, 'NODE_DESCRIPTION', None)
            or getattr(node_cls, '__doc__', None)
        )
        return desc.strip() if isinstance(desc, str) else None

    @staticmethod
    def get_node_tags(node_cls: NodeCls) -> List[str]:
        """
        Resolve the tag list for a node class.

        Priority:
            1. ``node_tags``   (ClassVar from BaseControlNode)
            2. ``NODE_TAGS``   (legacy uppercase convention)
        
        Args:
            node_cls: The node class to inspect.
            
        Returns:
            List of tag strings (may be empty).
        """
        tags = getattr(node_cls, 'node_tags', None) or getattr(node_cls, 'NODE_TAGS', None)
        if tags is None:
            return []
        if isinstance(tags, (list, tuple)):
            return [str(t) for t in tags]
        return [str(tags)]

    def get_node_icon(self, node_cls: NodeCls) -> Optional[Any]:
        """
        Resolve and cache a ``QIcon`` for a node class.

        Reads the ``node_icon`` class variable, loads the icon once, and
        caches it for all subsequent calls.  Failed lookups are also
        cached (as a miss sentinel) so we never retry a broken path.

        Args:
            node_cls: The node class to inspect.

        Returns:
            A ``QIcon`` instance if the class defines a valid ``node_icon``,
            otherwise ``None``.
        """
        icon_path = getattr(node_cls, 'node_icon', None)
        if not icon_path:
            return None

        # Check cache (hits AND misses)
        cached = self._icon_cache.get(icon_path)
        if cached is self._ICON_MISS:
            return None
        if cached is not None:
            return cached

        # Build icon
        QIcon = _ensure_qicon()
        try:
            # Qt resource paths (:/…) are valid if the resource was compiled in
            if icon_path.startswith(":/") or os.path.isfile(icon_path):
                icon = QIcon(icon_path)
                if not icon.isNull():
                    self._icon_cache[icon_path] = icon
                    return icon
        except Exception:
            pass

        # Mark as miss
        self._icon_cache[icon_path] = self._ICON_MISS
        return None

    def clear_icon_cache(self) -> None:
        """
        Clear the icon cache.
        
        Useful after changing icon paths or resource files at runtime.
        """
        self._icon_cache.clear()

    def fuzzy_search(
        self,
        query: str,
        *,
        threshold: float = 0.6,
        limit: Optional[int] = None,
    ) -> List[SearchResult]:
        """
        Perform fuzzy search using simple character-based similarity.
        
        This is useful when users might misspell node names.
        
        Args:
            query: The search string.
            threshold: Minimum similarity ratio (0.0 to 1.0) to include results.
            limit: Maximum number of results to return.
            
        Returns:
            List of SearchResult objects sorted by similarity score.
        """
        if not query:
            return []
        
        query_lower = query.lower()
        results: List[SearchResult] = []
        
        for category, sub_dict in self._tree.items():
            for subcategory, node_list in sub_dict.items():
                for node_cls in node_list:
                    # Calculate similarity against class name
                    class_name = node_cls.__name__.lower()
                    similarity = self._similarity_ratio(query_lower, class_name)
                    
                    # Also check display name
                    display_name = getattr(node_cls, 'node_name', None) or getattr(node_cls, 'NODE_NAME', None)
                    if display_name:
                        display_similarity = self._similarity_ratio(query_lower, str(display_name).lower())
                        similarity = max(similarity, display_similarity)
                    
                    if similarity >= threshold:
                        results.append(SearchResult(
                            node_cls=node_cls,
                            score=similarity * 10,  # Scale to be comparable with regular search
                            matched_fields=['fuzzy'],
                            category=category,
                            subcategory=subcategory,
                        ))
        
        results.sort(key=lambda r: -r.score)
        
        if limit is not None and limit > 0:
            results = results[:limit]
        
        return results

    @staticmethod
    def _similarity_ratio(s1: str, s2: str) -> float:
        """
        Calculate similarity ratio between two strings.
        Uses a simple longest common subsequence approach.
        
        Returns:
            Float between 0.0 (no similarity) and 1.0 (identical).
        """
        if not s1 or not s2:
            return 0.0
        
        if s1 == s2:
            return 1.0
        
        # Simple character overlap ratio
        len1, len2 = len(s1), len(s2)
        
        # Count matching characters (allowing for different positions)
        matches = 0
        s2_chars = list(s2)
        
        for char in s1:
            if char in s2_chars:
                matches += 1
                s2_chars.remove(char)
        
        # Calculate ratio based on the longer string
        return (2.0 * matches) / (len1 + len2)


# --- Global Singleton ---
NODE_REGISTRY = NodeRegistry()


def register_node(cls: NodeCls) -> NodeCls:
    """
    Decorator to register a node class.
    
    Usage:
        @register_node
        class MyNode(BaseNode):
            node_class = "Math"
            node_subclass = "Trig"  # Optional
            node_description = "Performs trigonometric calculations"  # Optional
            node_tags = ["sin", "cos", "tan"]  # Optional
            ...
    """
    return NODE_REGISTRY.register(cls)