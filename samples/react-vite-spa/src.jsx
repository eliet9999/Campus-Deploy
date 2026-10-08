import React, {useState} from 'react';
import {createRoot} from 'react-dom/client';
import './style.css';
function App(){const [count,setCount]=useState(0);const [path,setPath]=useState(location.pathname);return <main><p className="tag">CAMPUS LAB / REACT + VITE</p><h1>정적인 배포.<br/>살아 있는 인터랙션.</h1><p>현재 경로: <strong>{path}</strong></p><button onClick={()=>setCount(count+1)}>클릭 횟수: {count}</button><a href="/about" onClick={e=>{e.preventDefault();history.pushState({},'', '/about');setPath('/about')}}>소개 페이지로 이동 →</a><p>npm으로 빌드한 React 앱입니다.</p></main>};
createRoot(document.getElementById('root')).render(<App/>);
