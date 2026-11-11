import core from "./es/core";
import settings from "./es/settings";
import system from "./es/system";
import mail from "./es/mail";
import tenant from "./es/tenant";
import hosting from "./es/hosting";
import filemanager from "./es/filemanager";
import domains from "./es/domains";
import apps from "./es/apps";
import appsServices from "./es/apps-services";

const messages: Record<string, string> = {
  ...core,
  ...settings,
  ...system,
  ...mail,
  ...tenant,
  ...hosting,
  ...filemanager,
  ...domains,
  ...apps,
  ...appsServices,
};

export default messages;
