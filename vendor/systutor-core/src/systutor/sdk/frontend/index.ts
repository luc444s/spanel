export type PluginComponent = () => JSX.Element | null;

export type PluginRoute = {
  path: string;
  title: string;
  component: PluginComponent;
  requiredPermissions?: string[];
};

export type PluginNavigationItem = {
  to: string;
  label: string;
  requiredPermissions?: string[];
  group?: string;
};

export type PluginWidget = {
  id: string;
  slot: "system.dashboard";
  title: string;
  component: PluginComponent;
  requiredPermissions?: string[];
};

export type PluginFrontendRegistration = {
  pluginId: string;
  routes: PluginRoute[];
  navigation: PluginNavigationItem[];
  widgets: PluginWidget[];
};

export type PluginFrontendContext = {
  appBasePath: string;
};
