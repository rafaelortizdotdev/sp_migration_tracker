"""
Test script for Phase 2 - Virtual Splitting
"""
import requests
import json

BASE_URL = "http://localhost:8000/api"


def test_split_preview():
    """Test split preview endpoint"""
    print("\n=== Testing Split Preview ===")
    
    # Find a Hybrid SP
    response = requests.get(f"{BASE_URL}/sps?category=Hybrid")
    sps = response.json()
    
    if not sps:
        print("No Hybrid SPs found")
        return None
    
    sp_name = sps[0]['name']
    print(f"Testing with: {sp_name}")
    
    # Get split preview
    response = requests.get(f"{BASE_URL}/nodes/{sp_name}/split-preview")
    preview = response.json()
    
    print(f"\nCan split: {preview['can_split']}")
    print(f"\nORCH Preview:")
    print(f"  Name: {preview['orch_preview']['name']}")
    print(f"  Calls: {preview['orch_preview']['calls']}")
    print(f"\nLOGIC Preview:")
    print(f"  Name: {preview['logic_preview']['name']}")
    print(f"  Target Tables: {preview['logic_preview']['target_tables']}")
    
    return sp_name


def test_split(sp_name):
    """Test actual split"""
    print(f"\n=== Testing Split: {sp_name} ===")
    
    response = requests.post(
        f"{BASE_URL}/nodes/{sp_name}/split",
        json={"user": "test_user"}
    )
    
    if response.status_code == 200:
        result = response.json()
        print(f"✓ Split successful!")
        print(f"  Original hidden: {result['original_hidden']}")
        print(f"  Created nodes:")
        for node in result['created_nodes']:
            print(f"    - {node['name']} ({node['type']})")
        if result['warnings']:
            print(f"  Warnings:")
            for warning in result['warnings']:
                print(f"    ! {warning}")
        return True
    else:
        print(f"✗ Split failed: {response.status_code}")
        print(f"  {response.json()}")
        return False


def test_list_virtual_nodes():
    """Test listing virtual nodes"""
    print("\n=== Testing Virtual Nodes List ===")
    
    response = requests.get(f"{BASE_URL}/virtual-nodes")
    nodes = response.json()
    
    print(f"Found {len(nodes)} virtual nodes:")
    for node in nodes:
        print(f"  - {node['name']} ({node['node_type']}) from {node['parent_sp_name']}")


def test_merged_list():
    """Test merged SP list (source + virtual)"""
    print("\n=== Testing Merged SP List ===")
    
    response = requests.get(f"{BASE_URL}/sps")
    sps = response.json()
    
    virtual_count = sum(1 for sp in sps if sp.get('is_virtual'))
    source_count = len(sps) - virtual_count
    
    print(f"Total SPs: {len(sps)}")
    print(f"  Source SPs: {source_count}")
    print(f"  Virtual nodes: {virtual_count}")


def test_unsplit(sp_name):
    """Test unsplit"""
    print(f"\n=== Testing Unsplit: {sp_name} ===")
    
    response = requests.post(
        f"{BASE_URL}/nodes/{sp_name}/unsplit",
        json={"user": "test_user"}
    )
    
    if response.status_code == 200:
        result = response.json()
        print(f"✓ Unsplit successful!")
        print(f"  {result['message']}")
        return True
    else:
        print(f"✗ Unsplit failed: {response.status_code}")
        print(f"  {response.json()}")
        return False


if __name__ == "__main__":
    print("=" * 60)
    print("Phase 2 - Virtual Splitting Test Suite")
    print("=" * 60)
    
    try:
        # Test 1: Preview
        sp_name = test_split_preview()
        
        if sp_name:
            # Test 2: Split
            if test_split(sp_name):
                # Test 3: List virtual nodes
                test_list_virtual_nodes()
                
                # Test 4: Merged list
                test_merged_list()
                
                # Test 5: Unsplit
                test_unsplit(sp_name)
                
                # Verify cleanup
                test_list_virtual_nodes()
        
        print("\n" + "=" * 60)
        print("✓ All tests completed!")
        print("=" * 60)
        
    except requests.exceptions.ConnectionError:
        print("\n✗ Error: Server not running!")
        print("Start server with: uvicorn backend.main:app --reload")
    except Exception as e:
        print(f"\n✗ Error: {e}")
