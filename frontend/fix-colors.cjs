const fs = require('fs');
const path = require('path');

const filesToFix = [
  'src/components/Editor/OutlineMasterEditor.tsx',
  'src/components/Editor/ChapterOutlineDetail.tsx',
  'src/components/Editor/SceneBriefPanel.tsx',
  'src/components/Editor/ThreadPlanPanel.tsx',
  'src/components/Layout/Sidebar.tsx',
  'src/pages/ProjectWorkspace.tsx',
  'src/components/Common/ExportPanel.tsx'
];

const colorMap = {
  'text-surface-950': 'text-pine-950',
  'text-surface-900': 'text-pine-950',
  'text-surface-800': 'text-pine-900',
  'text-surface-700': 'text-pine-900',
  'text-surface-600': 'text-pine-800',
  'text-surface-500': 'text-pine-700',
  'text-surface-400': 'text-pine-700',
  'text-surface-300': 'text-pine-600',
  'text-surface-200': 'text-pine-600',
  'text-surface-100': 'text-pine-500',
  'text-surface-50': 'text-pine-500',
  
  'bg-surface-950': 'bg-pine-50/90',
  'bg-surface-900': 'bg-pine-50/80',
  'bg-surface-800': 'bg-pine-50/70',
  
  'border-surface-800': 'border-pine-200',
  'border-surface-700': 'border-pine-200',
};

filesToFix.forEach(file => {
  const fullPath = path.join(__dirname, file);
  if (fs.existsSync(fullPath)) {
    let content = fs.readFileSync(fullPath, 'utf8');
    
    // Replace text-surface-*
    content = content.replace(/text-surface-(\d+)/g, (match, p1) => {
      return colorMap[`text-surface-${p1}`] || match;
    });

    // Also replace any bg-surface-* that I missed
    content = content.replace(/bg-surface-(\d+)(\/\d+)?/g, (match, p1, p2) => {
        if (p1 >= 800) {
            return `bg-white${p2 || ''}`; // Make dark backgrounds white
        }
        return match;
    });

    fs.writeFileSync(fullPath, content, 'utf8');
    console.log(`Fixed ${file}`);
  }
});
