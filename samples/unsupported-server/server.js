import express from 'express';
express().get('/', (_, res) => res.send('This server app is not supported by Campus Deploy P0.')).listen(3000);
