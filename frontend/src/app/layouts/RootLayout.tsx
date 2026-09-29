import { Outlet } from 'react-router'
import { AppFrame } from './AppFrame'

export function RootLayout() {
  return (
    <AppFrame>
      <Outlet />
    </AppFrame>
  )
}
