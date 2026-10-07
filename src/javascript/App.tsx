import { ReactElement } from 'react'
import { BrowserRouter, Routes, Route, Navigate } from 'react-router-dom'
import { AuthProvider, useAuth } from './context/AuthContext'
import Login from './pages/Login'
import Home from './pages/Home'
import BatchOrganize from './pages/BatchOrganize'
import Bookshelf from './pages/Bookshelf'
import Gallery from './pages/Gallery'
import ImageGenerate from './pages/ImageGenerate'
import LLMPage from './pages/LLMPage'
import Metadata from './pages/Metadata'
import LoraDataset from './pages/LoraDataset'
import MangaDraft from './pages/MangaDraft'
import MangaImport from './pages/MangaImport'
import CharacterDataset from './pages/CharacterDataset'
import Chunks from './pages/Chunks'
import DbStatus from './pages/DbStatus'
import Selection from './pages/Selection'
import Story from './pages/Story'
import StoryLibrary from './pages/StoryLibrary'
import Writer from './pages/Writer'

function ProtectedRoute({ children }: { children: ReactElement }) {
  const { isAuthenticated } = useAuth()
  return isAuthenticated ? children : <Navigate to="/login" replace />
}

function AppRoutes() {
  const { isAuthenticated } = useAuth()
  return (
    <Routes>
      <Route
        path="/login"
        element={isAuthenticated ? <Navigate to="/" replace /> : <Login />}
      />
      <Route
        path="/"
        element={<ProtectedRoute><Home /></ProtectedRoute>}
      />
      <Route
        path="/generate"
        element={<ProtectedRoute><ImageGenerate /></ProtectedRoute>}
      />
      <Route
        path="/metadata"
        element={<ProtectedRoute><Metadata /></ProtectedRoute>}
      />
      <Route
        path="/batch"
        element={<ProtectedRoute><BatchOrganize /></ProtectedRoute>}
      />
      <Route
        path="/llm"
        element={<ProtectedRoute><LLMPage /></ProtectedRoute>}
      />
      <Route
        path="/lora-dataset"
        element={<ProtectedRoute><LoraDataset /></ProtectedRoute>}
      />
      <Route
        path="/character-dataset"
        element={<ProtectedRoute><CharacterDataset /></ProtectedRoute>}
      />
      <Route
        path="/chunks"
        element={<ProtectedRoute><Chunks /></ProtectedRoute>}
      />
      <Route
        path="/db-status"
        element={<ProtectedRoute><DbStatus /></ProtectedRoute>}
      />
      <Route
        path="/select"
        element={<ProtectedRoute><Selection /></ProtectedRoute>}
      />
      <Route
        path="/story"
        element={<ProtectedRoute><Story /></ProtectedRoute>}
      />
      <Route
        path="/writer"
        element={<ProtectedRoute><Writer /></ProtectedRoute>}
      />
      <Route
        path="/manga-draft"
        element={<ProtectedRoute><MangaDraft /></ProtectedRoute>}
      />
      <Route
        path="/manga-import"
        element={<ProtectedRoute><MangaImport /></ProtectedRoute>}
      />
      <Route
        path="/story-library"
        element={<ProtectedRoute><StoryLibrary /></ProtectedRoute>}
      />
      <Route
        path="/gallery"
        element={<ProtectedRoute><Gallery /></ProtectedRoute>}
      />
      <Route
        path="/bookshelf"
        element={<ProtectedRoute><Bookshelf /></ProtectedRoute>}
      />
      <Route path="*" element={<Navigate to="/" replace />} />
    </Routes>
  )
}

export default function App() {
  return (
    <AuthProvider>
      <BrowserRouter>
        <AppRoutes />
      </BrowserRouter>
    </AuthProvider>
  )
}
