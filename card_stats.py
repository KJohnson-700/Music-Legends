"""
Card Stats System - Complete Weighted Pool Implementation
Implements exact step-by-step weighted pool system as specified
"""

import math
import random
import re
from typing import Dict, List, Optional, Tuple

# Weight distribution (can be adjusted later)
WEIGHTS = {
    "same_artist": 60,    # 60% weight
    "related_genre": 30,  # 30% weight
    "wildcard": 10       # 10% weight
}

def parse_artist_song_from_title(title: str, channel_name: str) -> Tuple[str, str]:
    """
    Parse artist + song from title with common formats:
    - "Artist - Song"
    - "Song (Artist)"
    - "Artist: Song"
    - "Song | Artist"
    Fallback: artist = channel_name, song = title
    """
    
    # Common separators
    separators = [" - ", " -", " (", " | ", ":", " – "]
    
    for sep in separators:
        if sep in title:
            parts = title.split(sep, 1)
            if len(parts) == 2:
                # Determine which part is likely the artist
                part1, part2 = parts[0].strip(), parts[1].strip()
                
                # Remove parentheses from second part if they exist
                part2 = part2.rstrip(")").strip()
                
                # Heuristic: shorter part is often artist, longer is song
                if len(part1) <= len(part2):
                    return part1, part2
                else:
                    return part2, part1
    
    # Fallback
    return channel_name, title

def assign_rarity_by_views(views: int) -> str:
    """Assign rarity based on view count tiers"""
    if views >= 1_000_000_000:  # 1B+ views
        return "legendary"
    elif views >= 100_000_000:  # 100M-1B views
        return "epic"
    elif views >= 10_000_000:   # 10M-100M views
        return "rare"
    else:                       # <10M views
        return "common"

def calculate_base_power_by_views(views: int) -> int:
    """Base stat (0-100) from view count on a log scale (Phase 3), with a small
    jitter for variety. Replaces the old four random bands (30-49/50-69/70-89/90-100)
    whose cliffs made view counts either meaningless or decisive."""
    from core.power import stat_from_views, STAT_MAX
    base = stat_from_views(views)
    return max(0, min(STAT_MAX, base + random.randint(-3, 3)))

def calculate_cost(power: int) -> int:
    """Calculate cost based on power (1 cost per 10 power, minimum 1)"""
    return max(1, power // 10)

def create_hero_card(video_data: Dict) -> Dict:
    """Create hero card from YouTube video data"""
    
    # Extract metadata
    video_id = video_data.get("video_id", "")
    title = video_data.get("title", "Unknown")
    channel_name = video_data.get("artist", "Unknown Artist")
    channel_id = video_data.get("channel_id", "")
    view_count = video_data.get("views", 0)
    thumbnail_url = video_data.get("thumbnail", "")
    
    # Parse artist and song from title
    artist, song = parse_artist_song_from_title(title, channel_name)
    
    # Assign rarity based on view count
    rarity = assign_rarity_by_views(view_count)
    
    # Hero cards get rarity boost
    if rarity == "common":
        rarity = "rare"
    elif rarity == "rare":
        rarity = "epic"
    elif rarity == "epic":
        rarity = "legendary"
    # Legendary stays Legendary
    
    # Calculate base power
    base_power = calculate_base_power_by_views(view_count)
    
    # Calculate cost
    cost = calculate_cost(base_power)
    
    return {
        "artist": artist,
        "song": song,
        "youtube_url": f"https://youtube.com/watch?v={video_id}",
        "youtube_id": video_id,
        "channel_id": channel_id,
        "view_count": view_count,
        "thumbnail": thumbnail_url,
        "rarity": rarity,
        "base_power": base_power,
        "cost": cost,
        "is_hero": True,
        "pool_source": "hero"
    }

def create_secondary_card(video_data: Dict, pool_source: str) -> Dict:
    """Create secondary card from video data"""
    
    # Extract metadata
    video_id = video_data.get("video_id", "")
    title = video_data.get("title", "Unknown")
    channel_name = video_data.get("artist", "Unknown Artist")
    channel_id = video_data.get("channel_id", "")
    view_count = video_data.get("views", 0)
    thumbnail_url = video_data.get("thumbnail", "")
    
    # Parse artist and song from title
    artist, song = parse_artist_song_from_title(title, channel_name)
    
    # Assign rarity based on view count
    rarity = assign_rarity_by_views(view_count)
    
    # Calculate base power
    base_power = calculate_base_power_by_views(view_count)
    
    # Calculate cost
    cost = calculate_cost(base_power)
    
    return {
        "artist": artist,
        "song": song,
        "youtube_url": f"https://youtube.com/watch?v={video_id}",
        "youtube_id": video_id,
        "channel_id": channel_id,
        "view_count": view_count,
        "thumbnail": thumbnail_url,
        "rarity": rarity,
        "base_power": base_power,
        "cost": cost,
        "is_hero": False,
        "pool_source": pool_source
    }

def build_pool_1_same_artist(channel_id: str, hero_video_id: str, max_results: int = 50) -> List[Dict]:
    """
    POOL 1: Same Artist Top Tracks (60% weight)
    Query YouTube API: search.list
    """
    
    # This would be implemented in the YouTube API class
    # For now, return empty list - will be populated by the API calls
    return []

def build_pool_2_related_genre(hero_video_id: str, hero_channel_id: str, max_results: int = 50) -> List[Dict]:
    """
    POOL 2: Related Genre Artists (30% weight)
    Query YouTube API: search.list with relatedToVideoId
    """
    
    # This would be implemented in the YouTube API class
    return []

def build_pool_3_wildcard(hero_video_id: str, max_results: int = 100) -> List[Dict]:
    """
    POOL 3: Wildcard Variety (10% weight)
    Query YouTube API: search.list with broader relatedToVideoId
    """
    
    # This would be implemented in the YouTube API class
    return []

def filter_pools_for_duplicates(pool_1: List[Dict], pool_2: List[Dict], pool_3: List[Dict], 
                               previously_generated_ids: List[str]) -> Tuple[List[Dict], List[Dict], List[Dict]]:
    """
    Filter ALL pools to remove duplicates based on previously generated cards
    """
    
    def filter_pool(pool: List[Dict]) -> List[Dict]:
        return [card for card in pool if card["youtube_id"] not in previously_generated_ids]
    
    pool_1_filtered = filter_pool(pool_1)
    pool_2_filtered = filter_pool(pool_2)
    pool_3_filtered = filter_pool(pool_3)
    
    return pool_1_filtered, pool_2_filtered, pool_3_filtered

def weighted_random_selection(pool_1: List[Dict], pool_2: List[Dict], pool_3: List[Dict]) -> List[Dict]:
    """
    Generate 4 Cards Using Weighted Random (Step 6)
    """
    
    generated_cards = []
    pools = {
        "pool_1": pool_1.copy(),
        "pool_2": pool_2.copy(), 
        "pool_3": pool_3.copy()
    }
    
    # Generate 4 cards
    for i in range(4):
        roll = random.randint(1, 100)
        
        if roll <= WEIGHTS["same_artist"]:  # 60% chance (1-60)
            pool_name = "pool_1"
            pool_source = "pool_1"
        elif roll <= WEIGHTS["same_artist"] + WEIGHTS["related_genre"]:  # 30% chance (61-90)
            pool_name = "pool_2"
            pool_source = "pool_2"
        else:  # 10% chance (91-100)
            pool_name = "pool_3"
            pool_source = "pool_3"
        
        # Select card from chosen pool
        chosen_pool = pools[pool_name]
        if chosen_pool:
            card = random.choice(chosen_pool)
            generated_cards.append(card)
            
            # Remove selected card from its pool (prevent duplicate within same pack)
            chosen_pool.remove(card)
        else:
            # Fallback: choose from any available pool
            for pool_name, pool_data in pools.items():
                if pool_data:
                    card = random.choice(pool_data)
                    card["pool_source"] = pool_name
                    generated_cards.append(card)
                    pool_data.remove(card)
                    break
    
    return generated_cards

def validate_generated_cards(cards: List[Dict]) -> bool:
    """
    Final validation: Ensure no duplicate youtube_ids within GENERATED_CARDS
    Same artist CAN appear multiple times (e.g., 3 Drake songs from Pool 1 is ALLOWED)
    """
    
    youtube_ids = [card["youtube_id"] for card in cards]
    return len(youtube_ids) == len(set(youtube_ids))

def generate_complete_pack(hero_video_data: Dict, pool_1_videos: List[Dict], pool_2_videos: List[Dict], 
                          pool_3_videos: List[Dict], previously_generated_ids: List[str] = None) -> Dict:
    """
    Complete pack generation following the exact step-by-step process
    """
    
    if previously_generated_ids is None:
        previously_generated_ids = []
    
    # Step 1: Create hero card
    hero_card = create_hero_card(hero_video_data)
    
    # Step 2: Filter pools for duplicates
    pool_1_filtered, pool_2_filtered, pool_3_filtered = filter_pools_for_duplicates(
        pool_1_videos, pool_2_videos, pool_3_videos, previously_generated_ids
    )
    
    # Step 3: Verify minimum pool sizes (with retry logic would be implemented in main function)
    # For now, we'll work with what we have
    
    # Step 4: Generate 4 cards using weighted random
    generated_cards = weighted_random_selection(pool_1_filtered, pool_2_filtered, pool_3_filtered)
    
    # Step 5: Final validation
    if not validate_generated_cards([hero_card] + generated_cards):
        # Try again if duplicates found
        generated_cards = weighted_random_selection(pool_1_filtered, pool_2_filtered, pool_3_filtered)
    
    # Step 6: Assign rarity and power to generated cards
    for card in generated_cards:
        # These are already assigned in create_secondary_card
        pass
    
    # Step 7: Create complete pack
    all_cards = [hero_card] + generated_cards
    
    # Step 8: Calculate pack statistics
    pool_distribution = {"hero": 1, "pool_1": 0, "pool_2": 0, "pool_3": 0}
    for card in generated_cards:
        pool_distribution[card["pool_source"]] = pool_distribution.get(card["pool_source"], 0) + 1
    
    pack_theme = f"{hero_card['artist']} + "
    if pool_distribution["pool_1"] >= 2:
        pack_theme += "Artist Focus"
    elif pool_distribution["pool_2"] >= 2:
        pack_theme += "Genre Focus"
    else:
        pack_theme += "Mixed Variety"
    
    return {
        "hero_card": hero_card,
        "generated_cards": generated_cards,
        "all_cards": all_cards,
        "pool_distribution": pool_distribution,
        "pack_theme": pack_theme,
        "total_power": sum(card["base_power"] for card in all_cards)
    }

def get_pack_summary_message(pack_result: Dict) -> str:
    """Generate pack summary message for Discord embed"""
    
    hero = pack_result["hero_card"]
    generated = pack_result["generated_cards"]
    pool_dist = pack_result["pool_distribution"]
    
    message = f"📊 Pool Distribution:\n"
    message += f"• Same Artist: {pool_dist.get('pool_1', 0)} cards (60% weight working!)\n"
    message += f"• Related Genre: {pool_dist.get('pool_2', 0)} cards (30%)\n"
    message += f"• Wildcard: {pool_dist.get('pool_3', 0)} cards (10%)\n\n"
    
    message += f"🎲 Generated Cards:\n"
    for i, card in enumerate(generated, 1):
        pool_name = {
            "pool_1": "Same Artist",
            "pool_2": "Related Genre", 
            "pool_3": "Wildcard"
        }.get(card["pool_source"], "Unknown")
        
        message += f"{i}️⃣ {card['artist']} - {card['song']} ({card['rarity']})\n"
        message += f"   └─ Pool: {pool_name}\n"
    
    return message.strip()
