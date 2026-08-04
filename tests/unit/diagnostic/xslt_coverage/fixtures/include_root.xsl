<?xml version='1.0'?>
<xsl:stylesheet version="1.0"
                xmlns:xsl="http://www.w3.org/1999/XSL/Transform"
                xmlns:marc="http://www.loc.gov/MARC21/slim"
                xmlns:bf="http://id.loc.gov/ontologies/bibframe/">

  <xsl:include href="include_child.xsl"/>

  <xsl:template match="marc:datafield[@tag='100']" mode="work">
    <bf:Agent/>
  </xsl:template>

</xsl:stylesheet>
