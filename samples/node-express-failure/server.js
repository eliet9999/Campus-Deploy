require('express');
console.error('INTENTIONAL_RUNTIME_FAILURE: npm ci succeeds, server exits before listening');
process.exit(17);
