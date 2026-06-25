import { useState, useMemo } from 'react';

export default function useSortableTable(data, defaultSortKey = null, defaultDirection = 'desc') {
  const [sortConfig, setSortConfig] = useState({ key: defaultSortKey, direction: defaultDirection });

  const sortedData = useMemo(() => {
    let sortableItems = [...(data || [])];
    if (sortConfig.key !== null) {
      sortableItems.sort((a, b) => {
        let aVal = typeof sortConfig.key === 'function' ? sortConfig.key(a) : a[sortConfig.key];
        let bVal = typeof sortConfig.key === 'function' ? sortConfig.key(b) : b[sortConfig.key];
        
        if (aVal === bVal) return 0;
        
        if (aVal == null) return 1;
        if (bVal == null) return -1;
        
        if (typeof aVal === 'string') {
          aVal = aVal.toLowerCase();
          bVal = bVal.toLowerCase();
        }
        
        if (aVal < bVal) {
          return sortConfig.direction === 'asc' ? -1 : 1;
        }
        if (aVal > bVal) {
          return sortConfig.direction === 'asc' ? 1 : -1;
        }
        return 0;
      });
    }
    return sortableItems;
  }, [data, sortConfig]);

  const requestSort = (key) => {
    let direction = 'asc';
    if (sortConfig.key === key && sortConfig.direction === 'asc') {
      direction = 'desc';
    }
    setSortConfig({ key, direction });
  };

  const getSortIcon = (key) => {
    if (sortConfig.key === key) {
      return sortConfig.direction === 'asc' ? ' \u2191' : ' \u2193';
    }
    return '';
  };

  return { items: sortedData, requestSort, sortConfig, getSortIcon };
}
